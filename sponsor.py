"""Korean 00/06/12/18 sponsor settlements at a safe league boundary."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from engine import SafetyStop
from vision import Matcher,decode_screen

KST=timezone(timedelta(hours=9))
FILES=('sponsor_title.png','sponsor_settle.png','sponsor_reward_title.png','sponsor_reward_confirm.png',
       'sponsor_home_tab.png','sponsor_home_play.png','sponsor_result_confirm.png')


def slot_key(now):
    if now.tzinfo is None:raise ValueError('후원사 일정 시간대가 필요합니다.')
    local=now.astimezone(KST)
    return local.replace(hour=local.hour//6*6,minute=0,second=0,microsecond=0).isoformat()


class Sponsor:
    def __init__(self,root,enabled=True,dry=False,clock=None):
        self.root=Path(root);self.path=self.root/'sponsor_state.json';self.dry=dry
        self.clock=clock or (lambda:datetime.now(KST));self.hold=False;self.events=[];self.finished=False;self.recovering=False
        self.data={'slots':{},'active':None};self.matchers={};self.candidate=None;self.frames=0
        self.since=None;self.next_action=float('-inf');self.last_action=None;self.nav_taps={}
        if not enabled or not all((self.root/'assets'/name).is_file() for name in FILES):return
        specs=[('sponsor',FILES[0],[150,145,420,115]),('settle',FILES[1],[190,1090,340,100]),
               ('reward',FILES[2],[265,415,210,80]),('confirm',FILES[3],[225,755,270,90]),
               ('home_tab',FILES[4],[170,1065,140,85]),('home_play',FILES[5],[310,1065,225,90]),
               ('result_confirm',FILES[6],[435,1135,155,90]),
               ('result_ball','ball_result_icon.png',[160,1095,45,45]),
               ('league','league_next_match.png',[190,550,350,110])]
        for key,name,roi in specs:self.matchers[key]=Matcher(self.root/'assets'/name,.91,roi)
        if not dry and self.path.is_file():self.data=json.loads(self.path.read_text(encoding='utf-8'))

    @property
    def active(self):return bool(self.data['active'])

    def save(self):
        if self.dry:return
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)

    def due(self):
        key=slot_key(self.clock())
        return key if key not in self.data['slots'] else None

    def scan(self,png):
        if not self.matchers or not isinstance(png,bytes):return None
        im=decode_screen(png)
        if im is None or im.shape!=(1280,720):return None
        found={key:m.find(png)[0] for key,m in self.matchers.items()}
        if found['reward'] and found['confirm']:return 'reward'
        if found['sponsor'] and found['settle']:return 'sponsor'
        if found['home_tab'] and found['home_play']:return 'home'
        if found['league']:return 'league'
        if found['result_ball'] and found['result_confirm']:return 'result'
        return None

    def action(self,name,point,now,limit=3):
        if now<self.next_action:return None
        count=self.nav_taps.get(name,0)
        if count>=limit:raise SafetyStop('후원사 화면 전환 미확인 · '+name+' · 추가 클릭 없이 확인 필요')
        self.nav_taps[name]=count+1;self.next_action=now+5
        return point

    def observe(self,png,now,stop):
        self.hold=False;self.finished=False;self.recovering=False
        if not self.matchers or stop.is_set():return None
        view=self.scan(png)
        self.frames=self.frames+1 if view and self.candidate==view else 1;self.candidate=view
        if self.active and self.data['slots'][self.data['active']]['state']=='routing' and view is None:
            # A queued trip must not hide login screens, ads or ongoing gameplay.
            # The regular bounded recovery owns these screens; no settlement
            # has been attempted yet and its durable intent remains unchanged.
            self.recovering=True;self.since=None;return None
        if not self.active:
            key=self.due()
            if not key or view not in ('home','league','result','sponsor'):return None
            self.hold=True
            if self.frames<2:return None
            self.data['active']=key;self.data['slots'][key]={'state':'routing'};self.save()
            self.since=now;self.nav_taps={};self.next_action=float('-inf')
            self.events.append('후원사 보상 · 한국시간 '+datetime.fromisoformat(key).strftime('%m-%d %H시')+' 정산 시작')
        self.hold=True
        if self.since is None:self.since=now
        if now-self.since>120:raise SafetyStop('후원사 정산 전환 미확인 · 중복 정산 없이 확인 필요')
        if self.frames<2 or now<self.next_action:return None
        key=self.data['active'];entry=self.data['slots'][key];state=entry['state']
        if view=='reward':
            if state not in ('settling','claimed'):raise SafetyStop('후원금 수령 경위 미확인 · 자동 확인 보류')
            if state=='settling':entry['state']='claimed';self.save();self.events.append('후원금 획득 팝업 확인 · 정산 버튼 추가 클릭 없음')
            return self.action('획득 확인',(360,803),now)
        if state=='settling':return None  # Durable claim intent forbids another settlement, including after restart.
        if state=='claimed':
            if view=='sponsor':return self.action('로비 복귀',(360,1247),now)
            if view=='home':
                entry['state']='done';entry['completed_at']=self.clock().isoformat();self.data['active']=None
                self.data['slots']={k:v for k,v in sorted(self.data['slots'].items())[-32:]};self.save()
                self.hold=False;self.finished=True;self.since=None
                self.events.append('후원사 보상 수령 완료 · 원래 모드로 복귀');return None
            return None
        if view=='sponsor':
            entry['state']='settling';entry['attempted_at']=self.clock().isoformat();self.save()
            self.next_action=now+5;self.events.append('후원금 정산 · 이번 시간대 1회 클릭')
            return (360,1145)
        if view=='home':return self.action('후원사 탭',(245,1120),now)
        if view=='league':return self.action('리그 로비 뒤로',(360,1247),now)
        if view=='result':return self.action('리그 결과 확인',(515,1177),now)
        return None
