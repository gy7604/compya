"""Ranking lobby sequence; counts completed consecutive-match batches, never individual games."""
import json
from pathlib import Path


def default_ranking():
    return dict(enabled=False,lobby='',refresh='',points='',paid='',series='',disabled='',proceed='',result='',confirm='',reward='',reward_confirm='',refresh_dialog='',points_confirm='',star_confirm='',allow_star=False)


def validate_ranking_draft(config):
    for key in ('enabled','allow_star'):
        if type(config.get(key,False)) is not bool:raise ValueError('랭킹 사용 설정이 올바르지 않습니다.')
    for key in default_ranking():
        if key not in ('enabled','allow_star') and not isinstance(config.get(key,''),str):
            raise ValueError('랭킹 이미지 연결 설정이 올바르지 않습니다.')


def validate_ranking(config,records):
    validate_ranking_draft(config)
    if not config['enabled']:return
    available={r['id']:r for r in records if r['enabled'] and r['template']}
    required={'lobby':'랭킹 로비 제목','refresh':'무료 갱신','points':'포인트 갱신','series':'활성 연속 경기',
              'disabled':'비활성 연속 경기','proceed':'라인업 경기 진행','result':'결과 제목','confirm':'결과 확인',
              'refresh_dialog':'즉시 갱신 팝업 제목','points_confirm':'팝업 P 아이콘/결제 좌표'}
    missing=[label for key,label in required.items() if not config.get(key)]
    if missing:raise ValueError('설정은 저장되어 있습니다. 랭킹 자동 실행 전에 다음 항목을 등록하세요: '+', '.join(missing))
    if type(config.get('allow_star',False)) is not bool:raise ValueError('별 갱신 사용 여부가 올바르지 않습니다.')
    if config.get('allow_star') and (not config.get('paid') or not config.get('star_confirm')):
        raise ValueError('별 갱신을 켜려면 로비 별 아이콘과 팝업 별 확인 이미지를 등록하세요.')
    keys=[config[key] for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','reward','reward_confirm','refresh_dialog','points_confirm','star_confirm') if config.get(key)]
    if len(keys)!=len(set(keys)) or any(key not in available for key in keys):
        raise ValueError('랭킹 이미지는 서로 다르게 선택하고 버튼 관리에서 사용을 켜세요.')
    if bool(config.get('reward')) != bool(config.get('reward_confirm')):
        raise ValueError('15회 보상 수령 이미지와 보상 팝업 확인 이미지를 함께 선택하세요.')
    if config.get('reward') and any(available[config[key]].get('click_mode')!='fixed' for key in ('reward','reward_confirm')):
        raise ValueError('보상 수령 항목은 글러브 중앙, 보상 팝업 항목은 확인 버튼으로 클릭 위치를 각각 지정하세요.')
    if config.get('reward') and not available[config['reward']].get('roi'):
        raise ValueError('15회 보상은 글러브·15·수령 가능 표시를 함께 캡처하고 검색 영역을 글러브 주변으로 지정하세요.')
    for key in ('points_confirm','star_confirm'):
        if config.get(key) and (not available[config[key]].get('roi') or available[config[key]].get('click_mode')!='fixed'):
            raise ValueError('갱신 확인은 해당 팝업 버튼의 재화 아이콘만 캡처하고 클릭 위치를 해당 버튼으로 지정하세요.')
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
        validate_ranking_draft(config)
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)


class RankingMachine:
    def __init__(self,config,records,target):
        validate_ranking(config,records)
        self.config=dict(default_ranking(),**config);config=self.config;self.records={r['id']:r for r in records}
        self.target=target;self.completed=0;self.phase='lobby';self.counts={};self.absent=0
        self.next_allowed=float('-inf');self.finished=False;self.events=[];self.hold=False
        self.reward=config.get('reward','');self.reward_confirm=config.get('reward_confirm','');self.reward_absent=0;self.target_reached=False
    def stable(self,key,found):
        return bool(key) and self.counts.get(key,0)>=3
    def observe(self,found,now):
        self.hold=False
        if not self.config['enabled']:return None
        if self.finished:self.hold=True;return None
        lobby_key,refresh,points,paid,series,disabled,proceed,result,confirm=(self.config[key] for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm'))
        for key in (lobby_key,refresh,points,paid,series,disabled,proceed,result,confirm):
            if key:self.counts[key]=self.counts.get(key,0)+1 if found.get(key,False) else 0
        dialog=self.config['refresh_dialog'];points_confirm=self.config['points_confirm'];star_confirm=self.config['star_confirm']
        for key in (self.reward,self.reward_confirm,dialog,points_confirm,star_confirm):
            if key:self.counts[key]=self.counts.get(key,0)+1 if found.get(key,False) else 0
        self.reward_absent=0 if found.get(self.reward,False) else self.reward_absent+1
        lobby=found.get(lobby_key,False)
        if self.phase=='playing':
            self.hold=lobby or found.get(result,False)
            if self.stable(result,found):
                self.completed+=1;self.events.append(f'랭킹 연속 경기 결과 확인 · 완료 {self.completed}/{self.target}회')
                self.hold=True
                if self.completed>=self.target:
                    self.target_reached=True
                    if not self.reward:self.finished=True;return None
                self.phase='result_confirm';self.counts={}
            return None
        self.hold=True
        if self.phase=='departing':
            self.absent=0 if lobby or found.get(proceed,False) else self.absent+1
            if self.absent>=3:self.phase='playing';self.events.append('랭킹 경기 화면 진입 확인 · 일반 경기 동작 재개')
            return None
        if now<self.next_allowed:return None
        key=None
        if self.phase=='refresh_return':
            self.absent=0 if found.get(dialog,False) else self.absent+1
            if self.stable(lobby_key,found) and self.absent>=3:
                self.phase='series';self.counts={};self.events.append('즉시 갱신 팝업 닫힘 · 연속 경기 활성화 대기')
            return None
        if self.phase in ('points_confirm','star_confirm'):
            currency=points_confirm if self.phase=='points_confirm' else star_confirm
            other=star_confirm if self.phase=='points_confirm' else points_confirm
            allowed=self.phase=='points_confirm' or self.config['allow_star']
            if allowed and self.stable(dialog,found) and self.stable(currency,found) and not found.get(other,False):
                key=currency;self.phase='refresh_return';self.absent=0;self.events.append('즉시 갱신 · 등록한 재화 확인 후 결제 버튼 1회 클릭')
        elif self.phase=='reward_return':
            if self.stable(lobby_key,found) and self.reward_absent>=3:
                self.phase='lobby';self.counts={};self.events.append('랭킹 15회 보상 팝업 종료 · 수령 가능 표시 사라짐 확인')
            return None
        elif self.phase=='reward_confirm':
            if self.stable(self.reward_confirm,found):key=self.reward_confirm;self.phase='reward_return'
        elif self.phase=='returning':
            if self.stable(lobby_key,found):self.phase='lobby';self.counts={}
            return None
        elif self.phase=='result_confirm':
            if self.stable(result,found) and self.stable(confirm,found):key=confirm;self.phase='returning'
        elif self.phase=='lobby':
            if self.stable(lobby_key,found):
                if self.reward and found.get(self.reward,False):
                    if self.stable(self.reward,found):
                        key=self.reward;self.phase='reward_confirm';self.events.append('랭킹 15회 수령 가능 보상 확인 · 글러브 클릭')
                    else:return None
                elif self.target_reached:
                    if self.reward_absent>=3:self.finished=True;self.events.append('랭킹 목표 완료 · 보상 처리 후 중지')
                    return None
                # Positive disabled evidence is required before consuming a refresh.
                # Any active evidence immediately vetoes refresh, even before debounce.
                elif found.get(series,False):
                    if self.stable(series,found) and not found.get(disabled,False):
                        key=series;self.phase='lineup';self.events.append('랭킹 연속 경기 우선 · 무료 갱신 사용 안 함')
                elif self.stable(disabled,found):
                    if self.stable(refresh,found) and not found.get(paid,False):
                        key=refresh;self.phase='series';self.events.append('랭킹 연속 경기 비활성 확인 · 무료 갱신 1회')
                    elif self.stable(points,found) and not found.get(paid,False):
                        key=points;self.phase='points_confirm';self.events.append('랭킹 연속 경기 비활성 확인 · 일반 포인트 갱신 1회 (금액 숫자 미사용)')
                    elif self.config['allow_star'] and self.stable(paid,found) and not found.get(points,False) and not found.get(refresh,False):
                        key=paid;self.phase='star_confirm';self.events.append('랭킹 연속 경기 비활성 확인 · 허용된 별 갱신 팝업 열기')
        elif self.phase=='series' and self.stable(series,found):key=series;self.phase='lineup'
        elif self.phase=='lineup' and self.stable(proceed,found):key=proceed;self.phase='departing';self.absent=0
        if key:
            self.next_allowed=now+max(2.,self.records[key]['cooldown'])
            self.counts={}
        return key
