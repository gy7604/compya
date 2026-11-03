"""Claim the verified free league daily bonus, without touching the trophy shop."""
import json
from pathlib import Path
from engine import SafetyStop
from vision import Matcher,decode_screen

FILES=('league_bonus_ready.png','league_bonus_title.png','league_bonus_close.png','league_bonus_done.png')


class LeagueBonus:
    def __init__(self,root,enabled=True,dry=False):
        self.root=Path(root);self.path=self.root/'league_bonus_state.json';self.dry=dry
        self.hold=False;self.events=[];self.phase='idle';self.since=None;self.candidate=None;self.frames=0
        self.matchers={};self.popup_since=None
        if not enabled or not all((self.root/'assets'/name).is_file() for name in FILES):return
        for key,name,roi in [('ready',FILES[0],[165,1145,140,60]),('title',FILES[1],[200,325,330,90]),
                             ('close',FILES[2],[315,1180,90,90]),('done',FILES[3],[165,1145,140,60])]:
            self.matchers[key]=Matcher(self.root/'assets'/name,.91,roi)
        self.matchers['lobby']=Matcher(self.root/'assets/league_next_match.png',.91,[190,550,350,110])
        if not dry and self.path.is_file():
            self.phase=json.loads(self.path.read_text(encoding='utf-8')).get('phase','idle')

    def reserve(self,phase,now):
        if not self.dry:
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps({'phase':phase}),encoding='utf-8');temp.replace(self.path)
        self.phase=phase;self.since=now

    def observe(self,png,now,stop):
        self.hold=False
        if not self.matchers or stop.is_set() or not isinstance(png,bytes):return None
        image=decode_screen(png)
        if image is None or image.shape!=(1280,720):return None
        found={key:m.find(png)[0] for key,m in self.matchers.items()}
        popup=found['title'] and found['close']
        ready=found['lobby'] and found['ready'] and not found['done']
        done=found['lobby'] and found['done'] and not found['ready']
        token='popup' if popup else 'ready' if ready else 'done' if done else None
        self.frames=self.frames+1 if token and token==self.candidate else 1;self.candidate=token
        if not popup:self.popup_since=None
        if popup or ready or self.phase!='idle':self.hold=True
        if self.phase!='idle' and self.since is None:self.since=now
        if self.frames>=2 and done:
            if self.phase!='idle':
                self.reserve('idle',now);self.events.append('리그 데일리 보너스 · 미션 완료 표시 확인 · 수령 완료')
            self.hold=False;return None
        if self.phase!='idle' and now-self.since>45:
            raise SafetyStop('리그 데일리 보너스 수령 전환 미확인 · 중복 수령 없이 정지')
        if popup:
            if self.popup_since is None:self.popup_since=now
            if self.frames>=2 and now-self.popup_since>=5 and self.phase!='closing':
                self.reserve('closing',now);self.events.append('리그 데일리 보너스 · 보상 확인 후 닫기')
                return (360,1222)
        elif ready and self.frames>=2 and self.phase=='idle':
            self.reserve('opening',now);self.events.append('리그 데일리 보너스 · 100/100 확인 · 무료 보상 수령')
            return (350,1170)
        return None
