"""Opt-in periodic tap only while both watched images are absent and workflow idle."""
import json
import math
from pathlib import Path
from clicks import click_point


def default_progress():
    return dict(enabled=False, watch=[], point=None, size=None, delay=3., interval=5.)


def validate_progress(config, records):
    if type(config.get('enabled')) is not bool:
        raise ValueError('진행 보조 클릭 사용 여부가 올바르지 않습니다.')
    if not config['enabled']:
        return
    watch=config.get('watch',[])
    if len(watch)!=2 or len(set(watch))!=2:
        raise ValueError('미인식을 확인할 이미지 두 개를 다르게 선택하세요.')
    by_id={r['id']:r for r in records}
    for key in watch:
        record=by_id.get(key)
        if not record or not record['enabled'] or not record['template']:
            raise ValueError('선택한 두 이미지는 버튼 관리에서 등록하고 사용을 켜야 합니다.')
    for key,label in [('delay','미인식 대기'),('interval','클릭 간격')]:
        if not math.isfinite(float(config[key])) or not .5<=float(config[key])<=3600:
            raise ValueError(f'{label}는 0.5~3600초입니다.')
    size=config.get('size')
    if size is None or len(size)!=2 or any(type(x) is not int or x<=0 for x in size):
        raise ValueError('화면에서 보조 클릭 위치를 선택하세요.')
    click_point(dict(name='진행 보조 클릭',click_mode='fixed',click_point=config.get('point'),click_size=size),(0,0),size)


class ProgressStore:
    def __init__(self,root):
        self.path=Path(root)/'progress.json'

    def load(self):
        config=default_progress()
        if self.path.exists():config.update(json.loads(self.path.read_text(encoding='utf-8')))
        return config

    def save(self,config,records):
        validate_progress(config,records)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(self.path)


class ProgressClicker:
    def __init__(self,config,records):
        validate_progress(config,records)
        self.config=config
        self.since=None
        self.frames=0
        self.last_tap=float('-inf')
        self.taps=0

    def check_size(self,size):
        if self.config['enabled']:
            return click_point(dict(name='진행 보조 클릭',click_mode='fixed',click_point=self.config['point'],click_size=self.config['size']),(0,0),size)

    def observe(self,present,now,blocked=False):
        if not self.config['enabled']:
            return None
        if blocked or any(present.get(key,False) for key in self.config['watch']):
            self.since=None
            self.frames=0
            return None
        if self.since is None:self.since=now
        self.frames+=1
        if self.frames<3 or now-self.since<float(self.config['delay']) or now-self.last_tap<float(self.config['interval']):
            return None
        self.last_tap=now
        self.taps+=1
        return tuple(self.config['point'])
