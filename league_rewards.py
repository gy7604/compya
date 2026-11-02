"""Local season reward selection. No purchase or arbitrary popup actions."""
import json
import re
import subprocess
import tempfile
from pathlib import Path
import cv2
import numpy as np
from engine import SafetyStop
from vision import Matcher


def choose_reward(cards, remaining):
    if not cards or any(c.get('kind') not in ('player','craft','enhance') for c in cards):raise SafetyStop('시즌 보상 종류 미확인 · 선택/변경하지 않음')
    if remaining is not None and (type(remaining) is not int or not 0<=remaining<=4):raise SafetyStop('시즌 보상 변경 횟수 범위 미확인')
    for key in ('new', 'special'):
        chosen=next((i for i,c in enumerate(cards) if c.get(key) and c['kind']=='player'),None)
        if chosen is not None:return ('pick',chosen)
    if remaining is None:raise SafetyStop('시즌 보상 변경 횟수 미확인 · 임의 선택/변경 없이 확인 필요')
    if remaining>0:return ('reroll',None)
    for kind in ('player','craft','enhance'):
        chosen=next((i for i,c in enumerate(cards) if c['kind']==kind),None)
        if chosen is not None:return ('pick',chosen)
    raise SafetyStop('시즌 보상 종류 미확인 · 선택하지 않음')


class RewardPolicy:
    """Persist intent before I/O; never retry an ambiguous reroll or claim."""
    def __init__(self,path,dry=False):
        self.path=Path(path);self.dry=dry;self.data={};self.phase='idle';self.target=None
        self.candidate=None;self.frames=0;self.wait_since=None;self.events=[]
        if self.path.exists():self.data=json.loads(self.path.read_text(encoding='utf-8'))
        if self.data.get('pending'):self.phase=self.data['pending']
        self.target=self.data.get('target')

    def save(self):
        if self.dry:return
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)

    def observe(self,view,now):
        if view is None:return None
        if view.get('complete'):
            if self.data:
                self.data={};self.phase='idle';self.target=None;self.wait_since=None;self.save()
            return None
        token=json.dumps(view,sort_keys=True)
        if self.phase!='idle' and self.wait_since is None:self.wait_since=now
        self.frames=self.frames+1 if token==self.candidate else 1;self.candidate=token
        if self.frames<2:return None
        if self.wait_since is not None and now-self.wait_since>45:
            raise SafetyStop('시즌 보상 전환 미확인 · 중복 변경/수령 없이 확인 필요')
        if view.get('dialog'):
            if self.phase=='confirmed':return None
            if self.phase!='choose':
                raise SafetyStop('시즌 보상 선택 경위 미확인 · 확인 버튼을 누르지 않음')
            self.phase='confirmed';self.data['pending']='confirmed';self.save();self.wait_since=now
            self.events.append('시즌 보상 · 선택한 카드 수령 확정')
            return ('confirm',None)
        if not view.get('lobby'):return None
        board=view['board']
        if self.phase=='confirmed':return None
        if self.phase=='choose':return None
        if self.phase=='reroll':
            old=self.data['remaining'];current=view.get('remaining')
            changed=board!=self.data['board']
            if current==old-1 or (current is None and changed):
                self.data.update(pending='',remaining=old-1,board=board)
                self.phase='idle';self.wait_since=None;self.save()
            else:
                if self.wait_since is None:self.wait_since=now
                return None
        if self.phase=='pick':
            if view['cards']!=self.data['cards']:raise SafetyStop('시즌 보상 카드가 바뀌어 수령하지 않음')
            if view.get('selected')!=self.target:return None
            self.phase='choose';self.data['pending']='choose';self.save();self.wait_since=now
            return ('choose',None)
        remaining=view.get('remaining')
        if self.data.get('board')==board and 'remaining' in self.data:remaining=self.data['remaining']
        action,index=choose_reward(view['cards'],remaining)
        self.data.update(board=board,remaining=remaining,target=index,cards=view['cards'],pending=action)
        self.phase=action;self.target=index;self.save();self.wait_since=now
        if action=='reroll':
            self.events.append(f'시즌 보상 · NEW/시그니처/올스타 없음 · 남은 무료 변경 {remaining}회')
        else:
            card=view['cards'][index]
            label='NEW 선수' if card.get('new') else '시그니처·올스타' if card.get('special') else card['kind']
            self.events.append(f'시즌 보상 · {label} 선택 · {index+1}번 카드')
        return action,index


class LeagueRewards:
    def __init__(self,root,enabled=True,dry=False):
        self.root=Path(root);path=self.root/'league_rewards.json';self.enabled=False;self.hold=False
        self.events=[];self.last=float('-inf');self.view=None
        self.screen_since=None
        if not enabled or not path.exists():return
        self.cfg=json.loads(path.read_text(encoding='utf-8'));self.enabled=self.cfg.get('enabled',False)
        if not self.enabled:return
        self.matchers={key:Matcher(self.root/spec['path'],spec.get('threshold',.92),spec.get('roi')) for key,spec in self.cfg['screens'].items()}
        self.markers={key:[Matcher(self.root/path,.90) for path in paths] for key,paths in self.cfg['markers'].items()}
        self.policy=RewardPolicy(self.root/('league_reward_dry.json' if dry else 'league_reward_state.json'),dry)

    def text(self,image,stop):
        if stop.is_set():return ''
        with tempfile.TemporaryDirectory(prefix='compya_reward_') as folder:
            path=Path(folder)/'reward.png';cv2.imencode('.png',cv2.resize(image,None,fx=3,fy=3))[1].tofile(path)
            try:
                result=subprocess.run([str(self.root/'PriceOCR.exe'),str(path)],capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise OSError('OCR unavailable')
                try:return result.stdout.decode('utf-8').strip()
                except UnicodeDecodeError:return result.stdout.decode('cp949',errors='replace').strip()
            except (OSError,subprocess.TimeoutExpired) as error:raise SafetyStop('시즌 보상 글자 인식 실패 · 보상 변경/선택 보류') from error

    def scan(self,png,stop):
        matches={key:matcher.find(png)[0] for key,matcher in self.matchers.items()}
        if matches.get('complete'):return dict(complete=True)
        if matches.get('dialog'):return dict(dialog=True)
        if not matches.get('lobby'):return None
        im=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
        if (im.shape[1],im.shape[0])!=(720,1280):raise SafetyStop('시즌 보상 화면 크기가 등록값과 다릅니다.')
        cards=[];texts=[];selected=[]
        for index,(x,y,w,h) in enumerate(self.cfg['slots']):
            card=im[y:y+h,x:x+w];data=cv2.imencode('.png',card)[1].tobytes()
            hits={key:any(m.find(data)[0] for m in markers) for key,markers in self.markers.items()}
            if hits.get('craft') and hits.get('enhance'):raise SafetyStop('시즌 보상 종류가 중복 인식되어 선택하지 않음')
            kind='craft' if hits.get('craft') else 'enhance' if hits.get('enhance') else 'player'
            text=self.text(card,stop) if kind=='player' else kind
            normalized=re.sub(r'[^A-Z가-힣]','',text.upper())
            special=any(word in normalized for word in ('SIGNATURE','ALLSTAR','시그니처','올스타'))
            cards.append(dict(kind=kind,new=bool(hits.get('new')),special=special));texts.append(text)
            yellow=(card[:,:,2]>210)&(card[:,:,1]>180)&(card[:,:,0]<90)
            if yellow[:,:7].sum()+yellow[:,-7:].sum()>300:selected.append(index)
        x,y,w,h=self.cfg['counter_roi'];text=self.text(im[y:y+h,x:x+w],stop)
        numbers=re.findall(r'\d+',text);remaining=int(numbers[0]) if len(numbers)==1 and 0<=int(numbers[0])<=4 else None
        return dict(lobby=True,cards=cards,remaining=remaining,selected=selected[0] if len(selected)==1 else None,board=json.dumps([cards,texts],ensure_ascii=False))

    def observe(self,png,now,stop):
        self.hold=False
        if not self.enabled:return None
        # Only registered reward screens take precedence over ordinary actions.
        if not any(m.find(png)[0] for m in self.matchers.values()):
            self.screen_since=None;return None
        self.hold=True
        if self.screen_since is None:self.screen_since=now
        if now-self.screen_since>120:raise SafetyStop('시즌 보상 화면 확인 지연 · 임의 선택/변경 없이 확인 필요')
        if now-self.last<1:return None
        self.last=now;view=self.scan(png,stop)
        if stop.is_set():return None
        action=self.policy.observe(view,now)
        if view and view.get('complete'):self.hold=False
        while self.policy.events:self.events.append(self.policy.events.pop(0))
        if not action:return None
        kind,index=action
        if kind=='pick':
            x,y,w,h=self.cfg['slots'][index];return x+w//2,y+h//2
        return tuple(self.cfg['points'][kind])
