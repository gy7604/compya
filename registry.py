"""Persistent, portable button definitions and legacy configuration migration."""
import json
import math
import uuid
from pathlib import Path


def new_button(name='새 버튼'):
    return dict(id=uuid.uuid4().hex, name=name, enabled=False, template='',
                roi=None, size=None, threshold=.90, cooldown=2., disappear_timeout=8., kind='normal',
                click_mode='center', click_point=None, click_size=None)


def validate(records):
    names, ids = set(), set()
    for r in records:
        if not r['name'].strip() or r['name'].strip() in names:
            raise ValueError('버튼 이름은 비어 있거나 중복될 수 없습니다.')
        names.add(r['name'].strip())
        if r['id'] in ids:
            raise ValueError('중복된 버튼 ID입니다.')
        ids.add(r['id'])
        if r.get('kind', 'normal') not in ('normal', 'condition', 'step'):
            raise ValueError('이미지 용도가 올바르지 않습니다.')
        if r.get('click_mode', 'center') not in ('center', 'fixed'):
            raise ValueError('클릭 위치 방식이 올바르지 않습니다.')
        if r.get('click_mode') == 'fixed':
            point, click_size = r.get('click_point'), r.get('click_size')
            if point is None or len(point) != 2 or any(type(x) is not int or x < 0 for x in point):
                raise ValueError('지정 클릭 좌표 X,Y를 입력하세요.')
            if click_size is None or len(click_size) != 2 or any(type(x) is not int or x <= 0 for x in click_size):
                raise ValueError('화면에서 클릭 위치를 선택해 기준 해상도를 등록하세요.')
            if point[0] >= click_size[0] or point[1] >= click_size[1]:
                raise ValueError('클릭 좌표가 등록 화면을 벗어납니다.')
        if not isinstance(r['enabled'], bool):
            raise ValueError('사용 여부가 올바르지 않습니다.')
        if not .5 <= float(r['threshold']) <= 1:
            raise ValueError('인식 기준은 0.5~1입니다.')
        for key in ('cooldown', 'disappear_timeout'):
            if not math.isfinite(float(r[key])) or float(r[key]) < .5:
                raise ValueError('클릭 간격과 사라짐 제한은 0.5초 이상이어야 합니다.')
        roi = r.get('roi')
        if roi is not None and (len(roi) != 4 or any(type(x) is not int for x in roi)
                                or min(roi[:2]) < 0 or min(roi[2:]) <= 0):
            raise ValueError('검색 영역은 x,y,너비,높이 네 정수입니다.')
        size = r.get('size')
        if size is not None and (len(size) != 2 or any(type(x) is not int or x <= 0 for x in size)):
            raise ValueError('등록 해상도가 올바르지 않습니다.')
        if r['enabled'] and not r['template']:
            raise ValueError(f'{r["name"]}: 사용할 이미지를 먼저 등록하세요.')


class Registry:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root/'buttons.json'

    def resolve(self, path):
        p = Path(path)
        return p if p.is_absolute() else self.root/p

    def portable(self, path):
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(Path(path).resolve())

    def load(self, legacy):
        if self.path.exists():
            records = json.loads(self.path.read_text(encoding='utf-8'))['buttons']
            validate(records)
            return records
        records = []
        for name, key, roi_key in [('포수 리드', 'template', 'roi'), ('PLAY BALL', 'playball', 'playroi')]:
            r = new_button(name)
            path = legacy.get(key, str(self.root/'assets'/'catcher.png') if key == 'template' else '')
            r['template'] = self.portable(path) if path else ''
            r['enabled'] = bool(path)
            roi = legacy.get(roi_key)
            if isinstance(roi, str):
                roi = [int(x) for x in roi.split(',')] if roi.strip() else None
            r['roi'] = roi
            r['threshold'] = float(legacy.get('threshold', .9))
            if path:
                sidecar = self.resolve(path).with_suffix('.json')
                if sidecar.exists():
                    r['size'] = json.loads(sidecar.read_text(encoding='utf-8')).get('size')
            records.append(r)
        self.save(records)
        return records

    def save(self, records):
        validate(records)
        data = json.dumps({'version': 1, 'buttons': records}, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(data, encoding='utf-8')
        tmp.replace(self.path)
