"""Claim the verified free league daily bonus, without touching the trophy shop."""
import json
from pathlib import Path
from engine import SafetyStop
from vision import Matcher,OverlayMatcher,decode_screen

FILES=('league_bonus_ready.png','league_bonus_title.png','league_bonus_close_v2.png','league_bonus_done.png')


class LeagueBonus:
    def __init__(self,root,enabled=True,dry=False):
        self.root=Path(root);self.path=self.root/'league_bonus_state.json';self.dry=dry
        self.hold=False;self.events=[];self.phase='idle';self.since=None;self.candidate=None;self.frames=0
        self.matchers={};self.popup_since=None;self.close_attempts=0;self.last_close=None;self.last_matches={}
        if not enabled or not all((self.root/'assets'/name).is_file() for name in FILES):return
        for key,name,roi in [('ready',FILES[0],[165,1145,140,60]),('title',FILES[1],[200,325,330,90]),
                             ('close',FILES[2],[315,1180,90,90]),('done',FILES[3],[165,1145,140,60])]:
            # The clean X silhouette excludes the old yellow hand and ignores
            # the changing background. A matching bonus title is still required.
            self.matchers[key]=(OverlayMatcher(self.root/'assets'/name,.94,roi) if key=='close' else Matcher(self.root/'assets'/name,.91,roi))
        self.matchers['lobby']=Matcher(self.root/'assets/league_next_match.png',.91,[190,550,350,110])
        if not dry and self.path.is_file():
            saved=json.loads(self.path.read_text(encoding='utf-8'));self.phase=saved.get('phase','idle')
            self.close_attempts=saved.get('close_attempts',1 if self.phase=='closing' else 0)
            if self.phase not in ('idle','opening','closing') or type(self.close_attempts) is not int or not 0<=self.close_attempts<=3:
                raise ValueError('리그 데일리 보너스 이어받기 기록 확인 필요')

    def reserve(self,phase,now,restart_timer=True):
        if phase in ('idle','opening'):self.close_attempts=0;self.last_close=None
        if not self.dry:
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps({'phase':phase,'close_attempts':self.close_attempts}),encoding='utf-8');temp.replace(self.path)
        self.phase=phase
        if restart_timer:self.since=now

    def diagnostics(self):
        return dict(phase=self.phase,close_attempts=self.close_attempts,matches=self.last_matches)

    def observe(self,png,now,stop):
        self.hold=False
        if not self.matchers or stop.is_set() or not isinstance(png,bytes):return None
        image=decode_screen(png)
        if image is None or image.shape!=(1280,720):return None
        results={key:m.find(png) for key,m in self.matchers.items()}
        self.last_matches={key:dict(matched=value[0],score=round(value[2],4)) for key,value in results.items()}
        found={key:value[0] for key,value in results.items()}
        popup=found['title'] and found['close']
        ready=found['lobby'] and found['ready'] and not found['done']
        done=found['lobby'] and found['done'] and not found['ready']
        token='popup' if popup else 'ready' if ready else 'done' if done else None
        self.frames=self.frames+1 if token and token==self.candidate else 1;self.candidate=token
        if not popup:self.popup_since=None
        if popup or ready or self.phase!='idle':self.hold=True
        if self.phase!='idle' and self.since is None:self.since=now
        if self.phase=='closing' and self.last_close is None:self.last_close=now
        if self.frames>=2 and done:
            if self.phase!='idle':
                self.reserve('idle',now);self.events.append('리그 데일리 보너스 · 미션 완료 표시 확인 · 수령 완료')
            self.hold=False;return None
        if self.phase!='idle' and now-self.since>45:
            raise SafetyStop(f'리그 데일리 보너스 수령 전환 미확인 · {self.phase} · 창 제목={found["title"]} / 닫기 X={found["close"]} · 중복 수령 없이 정지')
        if popup:
            if self.popup_since is None:self.popup_since=now
            retry_due=self.last_close is None or now-self.last_close>=8
            if self.frames>=2 and now-self.popup_since>=5 and retry_due and self.close_attempts<3:
                restart_timer=self.phase!='closing';self.close_attempts+=1;self.last_close=now
                self.reserve('closing',now,restart_timer);self.events.append(f'리그 데일리 보너스 · 보상 확인 후 닫기 · {self.close_attempts}/3회')
                return (360,1222)
        elif ready and self.frames>=2 and self.phase=='idle':
            self.reserve('opening',now);self.events.append('리그 데일리 보너스 · 100/100 확인 · 무료 보상 수령')
            return (350,1170)
        return None
