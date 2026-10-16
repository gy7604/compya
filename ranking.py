"""Ranking lobby sequence; counts completed consecutive-match batches, never individual games."""
import json
from pathlib import Path


def default_ranking():
    return dict(enabled=False,lobby='',refresh='',points='',paid='',series='',disabled='',proceed='',result='',confirm='')


def validate_ranking(config,records):
    if not config['enabled']:return
    available={r['id']:r for r in records if r['enabled'] and r['template']}
    if any(not config.get(key) for key in ('lobby','refresh','series','disabled','proceed','result','confirm')):
        raise ValueError('랭킹 로비, 무료 갱신, 연속 경기 활성·비활성, 경기 진행, 결과 제목·확인 이미지를 선택하세요.')
    keys=[config[key] for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm') if config.get(key)]
    if len(keys)!=len(set(keys)) or any(key not in available for key in keys):
        raise ValueError('랭킹 이미지는 서로 다르게 선택하고 버튼 관리에서 사용을 켜세요.')
    if config.get('points') and not available[config['points']].get('roi'):
        raise ValueError('포인트 갱신은 갱신 버튼 안의 P 아이콘만 캡처하고 검색 영역을 해당 버튼 주변으로 지정하세요.')
    if any(available[key].get('kind','normal')=='normal' for key in keys):
        raise ValueError('랭킹 전용 이미지는 감지 전용 또는 규칙 전용으로 등록하세요. 일반 클릭과 중복하지 않습니다.')


class RankingStore:
    def __init__(self,root):self.path=Path(root)/'ranking.json'
    def load(self):
        config=default_ranking()
        if self.path.exists():config.update(json.loads(self.path.read_text(encoding='utf-8')))
        return config
    def save(self,config,records):
        validate_ranking(config,records)
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)


class RankingMachine:
    def __init__(self,config,records,target):
        validate_ranking(config,records)
        self.config=config;self.records={r['id']:r for r in records}
        self.target=target;self.completed=0;self.phase='lobby';self.counts={};self.absent=0
        self.next_allowed=float('-inf');self.finished=False;self.events=[];self.hold=False
    def stable(self,key,found):
        return bool(key) and self.counts.get(key,0)>=3
    def observe(self,found,now):
        self.hold=False
        if not self.config['enabled']:return None
        if self.finished:self.hold=True;return None
        lobby_key,refresh,points,paid,series,disabled,proceed,result,confirm=(self.config[key] for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm'))
        for key in (lobby_key,refresh,points,paid,series,disabled,proceed,result,confirm):
            if key:self.counts[key]=self.counts.get(key,0)+1 if found.get(key,False) else 0
        lobby=found.get(lobby_key,False)
        if self.phase=='playing':
            self.hold=lobby or found.get(result,False)
            if self.stable(result,found):
                self.completed+=1;self.events.append(f'랭킹 연속 경기 결과 확인 · 완료 {self.completed}/{self.target}회')
                self.hold=True
                if self.completed>=self.target:self.finished=True;return None
                self.phase='result_confirm';self.counts={}
            return None
        self.hold=True
        if self.phase=='departing':
            self.absent=0 if lobby or found.get(proceed,False) else self.absent+1
            if self.absent>=3:self.phase='playing';self.events.append('랭킹 경기 화면 진입 확인 · 일반 경기 동작 재개')
            return None
        if now<self.next_allowed:return None
        key=None
        if self.phase=='returning':
            if self.stable(lobby_key,found):self.phase='lobby';self.counts={}
            return None
        if self.phase=='result_confirm':
            if self.stable(result,found) and self.stable(confirm,found):key=confirm;self.phase='returning'
        elif self.phase=='lobby':
            if self.stable(lobby_key,found):
                # Positive disabled evidence is required before consuming a refresh.
                # Any active evidence immediately vetoes refresh, even before debounce.
                if found.get(series,False):
                    if self.stable(series,found) and not found.get(disabled,False):
                        key=series;self.phase='lineup';self.events.append('랭킹 연속 경기 우선 · 무료 갱신 사용 안 함')
                elif self.stable(disabled,found):
                    if self.stable(refresh,found) and not found.get(paid,False):
                        key=refresh;self.phase='series';self.events.append('랭킹 연속 경기 비활성 확인 · 무료 갱신 1회')
                    elif self.stable(points,found) and not found.get(paid,False):
                        key=points;self.phase='series';self.events.append('랭킹 연속 경기 비활성 확인 · 일반 포인트 갱신 1회 (금액 숫자 미사용)')
        elif self.phase=='series' and self.stable(series,found):key=series;self.phase='lineup'
        elif self.phase=='lineup' and self.stable(proceed,found):key=proceed;self.phase='departing';self.absent=0
        if key:
            self.next_allowed=now+max(2.,self.records[key]['cooldown'])
            self.counts={}
        return key
