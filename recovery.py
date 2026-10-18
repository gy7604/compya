"""Opt-in process restart and image-driven return route; no credentials or generic close taps."""
import json
import re
from pathlib import Path
from engine import SafetyStop

PACKAGE='com.com2us.futurecpb.android.google.global.normal'

def default_recovery():
    return dict(enabled=False,target='ranking',package=PACKAGE,goal='',steps=[],popups=[],max_launches=3,stall_enabled=False,stall_seconds=180,restart_player=True,player_console='',player_index=0)

def validate_draft(config):
    if config.get('target','ranking') not in ('ranking','league'):raise ValueError('복귀 대상 모드를 확인하세요.')
    if type(config.get('restart_player',True)) is not bool or not isinstance(config.get('player_console',''),str):raise ValueError('LDPlayer 재시작 설정을 확인하세요.')
    if type(config.get('player_index',0)) is not int or not 0<=config.get('player_index',0)<=999:raise ValueError('LDPlayer 번호는 0~999입니다.')
    if type(config.get('stall_enabled',False)) is not bool:raise ValueError('화면 멈춤 감시 설정을 확인하세요.')
    if type(config.get('stall_seconds',180)) is not int or not 60<=config.get('stall_seconds',180)<=1800:raise ValueError('멈춤 감지 시간은 60~1800초입니다.')
    if type(config.get('enabled')) is not bool:raise ValueError('재접속 사용 설정이 올바르지 않습니다.')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+',config.get('package','')):raise ValueError('게임 앱 패키지 이름이 올바르지 않습니다.')
    if type(config.get('max_launches')) is not int or not 1<=config['max_launches']<=10:raise ValueError('게임 재실행 한도는 1~10회입니다.')
    if not isinstance(config.get('steps'),list) or not isinstance(config.get('popups'),list):raise ValueError('복구 이미지 목록이 올바르지 않습니다.')
    refs=[config.get('goal','')]+config['steps']+config['popups']
    if any(not isinstance(key,str) for key in refs) or len(config['steps'])>30 or len(config['popups'])>20:raise ValueError('복구 이미지 목록을 확인하세요.')

def validate_recovery(config,records):
    validate_draft(config)
    if not config['enabled']:return
    if not config['goal'] or not config['steps']:raise ValueError('재접속 완료 이미지와 접속부터 목표 모드까지 진입 순서를 등록하세요.')
    refs=[config['goal']]+config['steps']+config['popups']
    available={r['id']:r for r in records if r['enabled'] and r['template']}
    if len(refs)!=len(set(refs)):raise ValueError('복구 완료·진입 단계·팝업 이미지는 서로 다르게 등록하세요.')
    for key in refs:
        r=available.get(key)
        if not r or (key!=config['goal'] and r.get('kind','normal')=='normal'):raise ValueError('재접속 이미지는 감지 전용 또는 규칙 전용으로 등록하고 사용을 켜세요.')
    for key in config['popups']:
        if available[key].get('click_mode')!='fixed':raise ValueError('광고는 고유한 팝업 이미지를 등록하고 닫기 클릭 위치를 지정하세요.')

def recovery_for_mode(config, ranking_enabled):
    """Only activate routes explicitly configured for the running mode."""
    effective = dict(config)
    mode='ranking' if ranking_enabled else 'league'
    if config.get('target','ranking') != mode:
        effective['enabled'] = False
    return effective

class RecoveryStore:
    def __init__(self,root):self.path=Path(root)/'recovery.json'
    def load(self):
        config=default_recovery()
        if self.path.exists():config.update(json.loads(self.path.read_text(encoding='utf-8')))
        return config
    def save(self,config):
        validate_draft(config);temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)

class RecoveryMachine:
    def __init__(self,config,records):
        validate_recovery(config,records);self.config=config;self.records={r['id']:r for r in records}
        self.active=False;self.running=True;self.hold=False;self.restored=False;self.launches=0
        self.last_launch=float('-inf');self.missing=0;self.index=0;self.pending=None;self.pending_type=None;self.suspended=None
        self.counts={};self.since={};self.absent=0;self.next_allowed=float('-inf');self.events=[]
    def poll(self,running,now):
        if not self.config['enabled']:return False
        self.running=running;self.missing=0 if running else self.missing+1
        if running:return False
        self.hold=True
        if self.missing<2:return False
        if not self.active:self.events.append('게임 종료 확인 · 자동 재접속 시작')
        self.active=True
        if now-self.last_launch<30:return False
        if self.launches>=self.config['max_launches']:raise SafetyStop('게임 재실행 한도 도달 · 추가 실행 없이 중지')
        self.launches+=1;self.last_launch=now;self.index=0;self.pending=None;self.suspended=None;self.counts={};self.since={};self.absent=0
        self.events.append(f'게임 재실행 요청 · {self.launches}/{self.config["max_launches"]}회')
        return True
    def restart_stalled(self,now):
        # Reuse the process-restart budget before issuing any force-stop command.
        self.missing=1
        return self.poll(False,now)

    def ready(self,key,now):
        return self.counts.get(key,0)>=3 and now-self.since.get(key,now)>=float(self.records[key].get('click_delay',0))
    def observe(self,found,now):
        self.restored=False;self.hold=False
        if not self.config['enabled']:return None
        if not self.running:self.hold=True;return None
        refs=[self.config['goal']]+self.config['steps']+self.config['popups']
        for key in refs:
            if found.get(key,False):
                if not self.counts.get(key,0):self.since[key]=now
                self.counts[key]=self.counts.get(key,0)+1
            else:self.counts[key]=0;self.since.pop(key,None)
        popups=[key for key in self.config['popups'] if found.get(key,False)]
        if popups or (found.get(self.config['steps'][0],False) and not found.get(self.config['goal'],False)):
            if not self.active:self.index=0;self.events.append('접속 화면 또는 광고 감지 · 모드 복귀 시작')
            self.active=True
        if not self.active:return None
        self.hold=True
        if self.pending and self.pending_type=='popup':
            self.absent=0 if found.get(self.pending,False) else self.absent+1
            if self.absent>=3:
                self.pending=None;self.absent=0
                if self.suspended:
                    self.pending,self.pending_type=self.suspended;self.suspended=None
            return None
        if popups:
            key=popups[0]
            if self.ready(key,now) and now>=self.next_allowed:
                if self.pending and self.pending_type=='step':self.suspended=(self.pending,self.pending_type)
                self.pending=key;self.pending_type='popup';self.absent=0
                self.next_allowed=now+max(2,float(self.records[key]['cooldown']))
                self.events.append(f'접속 광고 닫기 · {self.records[key]["name"]}')
                return key
            return None
        if self.counts.get(self.config['goal'],0)>=3:
            self.active=False;self.hold=True;self.restored=True;self.pending=None;self.index=0
            self.events.append('목표 모드 복귀 확인 · 완료 횟수 보존 후 매크로 재개');return None
        if now<self.next_allowed:return None
        if self.pending:
            next_key=self.config['steps'][self.index+1] if self.index+1<len(self.config['steps']) else self.config['goal']
            self.absent=0 if found.get(self.pending,False) else self.absent+1
            if self.absent>=3 or self.counts.get(next_key,0)>=3:
                self.index+=1;self.pending=None;self.absent=0
            return None
        if self.index>=len(self.config['steps']):return None
        key=self.config['steps'][self.index]
        if not found.get(key,False):
            later=[i for i in range(self.index+1,len(self.config['steps'])) if self.ready(self.config['steps'][i],now)]
            if len(later)==1:
                self.index=later[0];key=self.config['steps'][self.index]
                self.events.append('이미 진입한 접속 화면 확인 · 해당 단계부터 계속')
        if self.ready(key,now):
            self.pending=key;self.pending_type='step';self.absent=0
            self.next_allowed=now+max(2,float(self.records[key]['cooldown']))
            self.events.append(f'모드 진입 {self.index+1}/{len(self.config["steps"])} · {self.records[key]["name"]}')
            return key
        return None
