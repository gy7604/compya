"""Read the regular-season ROUND and keep it separate from session totals."""
import json
import tempfile
from datetime import datetime,timezone
from pathlib import Path
import cv2
import numpy as np
from engine import SafetyStop
from price_ocr import PriceReader
from vision import Matcher


class SeasonProgress:
    total=144

    def __init__(self,root,dry=False):
        self.path=Path(root)/('league_season_dry.json' if dry else 'league_season.json')
        self.data=None;self.verified=False;self.candidate=None;self.frames=0;self.persisted=True
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            round_,completed=data['round'],data['completed']
            if (type(round_) is int and type(completed) is int and 1<=round_<=144
                    and completed in (round_-1,round_) and data['phase'] in ('lobby','playing','completed')):
                self.data=data
        except (OSError,ValueError,KeyError,TypeError):pass

    def save(self):
        self.data['confirmed_at']=datetime.now(timezone.utc).isoformat()
        temp=self.path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(self.path);self.persisted=True
        except OSError:self.persisted=False

    def lobby(self,round_):
        if type(round_) is not int or not 1<=round_<=144:
            self.candidate=None;self.frames=0;return False
        self.frames=self.frames+1 if round_==self.candidate else 1
        self.candidate=round_
        if self.frames<2:return False
        # Repeated lobby frames must not erase a result or manufacture a new game.
        if self.verified and self.data and self.data['round']==round_:return False
        self.data=dict(round=round_,completed=round_-1,phase='lobby')
        self.verified=True;self.save();return True

    def playing(self):
        if not self.verified or not self.data or self.data['phase']!='lobby':return False
        self.data['phase']='playing';self.save();return True

    def result(self):
        if not self.verified or not self.data or self.data['phase']!='playing':return False
        self.data.update(completed=self.data['round'],phase='completed');self.save();return True

    def text(self,ranking=False):
        if not self.data:return '리그 시즌 · 현재 진행 확인 대기 (정규시즌 144경기)'
        data=self.data;round_,completed=data['round'],data['completed']
        prefix='리그 시즌' if self.verified else '리그 시즌 · 마지막 확인'
        if data['phase']=='playing':detail=f'{round_}번째 경기 진행 중'
        elif data['phase']=='lobby':detail=f'다음 {round_}번째 경기'
        else:detail='정규시즌 완료' if completed==144 else '경기 결과 확인'
        suffix=' · 랭킹 진행 중' if ranking else (' · 현재 화면 재확인 대기' if not self.verified else '')
        if not self.persisted:suffix+=' · 기록 저장 대기'
        return f'{prefix} · {completed}/144경기 완료 · {detail}{suffix}'


class RoundReader:
    """Two local OCR variants, then two distinct frame readings in SeasonProgress."""
    def __init__(self,root):
        root=Path(root);self.reader=PriceReader(root/'PriceOCR.exe')
        self.title=self.label=None;self.last=float('-inf')
        if all((root/'assets'/name).is_file() for name in ('league_next_match.png','league_round_label.png')):
            self.title=Matcher(root/'assets/league_next_match.png',.94,[200,535,320,85])
            self.label=Matcher(root/'assets/league_round_label.png',.94,[240,610,200,60])

    def observe(self,png,now,stop):
        if stop.is_set() or not self.title or now-self.last<1:return None
        try:
            found,_,_,size=self.title.find(png)
            if not found or size!=(720,1280):return None
            found,point,_,_=self.label.find(png)
            if not found:return None
            self.last=now
            frame=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
            x=point[0]+self.label.template.shape[1]//2+5;y=point[1]-20
            if not (0<=x<x+74<=720 and 0<=y<y+42<=1280):return None
            crop=cv2.resize(frame[y:y+42,x:x+74],None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
            with tempfile.TemporaryDirectory(prefix='compya_round_') as folder:
                values=[]
                for variant in (0,1):
                    if stop.is_set():return None
                    values.append(self.reader.read(crop,folder,variant))
            if stop.is_set() or values[0]!=values[1] or not 1<=values[0]<=144:return None
            return values[0]
        except (SafetyStop,OSError,ValueError,cv2.error):
            # Status recognition may fail; it must never stop gameplay or tap anything.
            return None
