"""Recognize ball purchase screens and insufficient league balls. Never buys."""
import re
import subprocess
import tempfile
from pathlib import Path
import cv2
from vision import Matcher, decode_screen


FILES=('ball_shop_description.png','ball_shop_icon.png','ball_result_icon.png','ball_result_disabled.png','ball_info_title.png','ball_info_icon.png')


def parse_balance(text):
    value=re.fullmatch(r'\s*(\d{1,3})\s*/\s*(\d{1,2})\s*',text)
    if not value:return None
    have,need=map(int,value.groups())
    return (have,need) if 0<=have<=999 and 1<=need<=20 else None


class BallGuard:
    def __init__(self,root):
        self.root=Path(root);self.hold=False;self.reason='';self.candidate=None;self.frames=0
        self.since=None;self.last=float('-inf');self.reading=None;self.layout=None
        specs=[(FILES[0],[65,535,190,70]),(FILES[1],[65,725,100,160]),
               (FILES[2],[160,1095,45,45]),(FILES[3],[105,1140,235,70]),
               (FILES[4],[245,250,235,75]),(FILES[5],[100,400,175,240])]
        self.matchers=[]
        if not all((self.root/'assets'/name).is_file() for name,_ in specs):return
        for name,roi in specs:
            self.matchers.append(Matcher(self.root/'assets'/name,.90,roi,color_tolerance=20))

    def read(self,png,stop):
        if stop.is_set():return None
        image=decode_screen(png,True)[1100:1140,198:260]
        with tempfile.TemporaryDirectory(prefix='compya_balls_') as folder:
            path=Path(folder)/'balance.png'
            cv2.imencode('.png',cv2.resize(image,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC))[1].tofile(path)
            try:
                result=subprocess.run([str(self.root/'PriceOCR.exe'),str(path)],capture_output=True,timeout=8,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                return parse_balance(result.stdout.decode('utf-8').strip()) if result.returncode==0 else None
            except (OSError,UnicodeError,subprocess.TimeoutExpired):return None

    def observe(self,png,now,stop):
        self.hold=False;self.reason='';self.layout=None
        if not self.matchers or stop.is_set() or not isinstance(png,bytes):return False
        screen=decode_screen(png)
        if screen is None or screen.shape!=(1280,720):return False
        detail=all(m.find(png)[0] for m in self.matchers[:2])
        info=not detail and all(m.find(png)[0] for m in self.matchers[4:6])
        shop=detail or info
        result=not shop and all(m.find(png)[0] for m in self.matchers[2:4])
        self.layout='detail' if detail else 'info' if info else 'result' if result else None
        if not shop and not result:
            self.candidate=None;self.frames=0;self.since=None;self.reading=None;return False
        # Hold on the first frame, before watchdogs, recovery, rewards or clicks.
        self.hold=True
        if self.since is None:self.since=now
        if shop:token='shop'
        else:
            if now-self.last<1:return False
            self.last=now;balance=self.read(png,stop)
            if stop.is_set():return False
            if balance is None:
                self.candidate=None;self.frames=0
                if now-self.since<15:return False
                self.reason='리그 볼 잔량 확인 필요 · 비활성 경기 버튼';return True
            if balance[0]>=balance[1]:
                self.hold=False;self.candidate=None;self.frames=0;self.since=None;return False
            token=balance
        self.frames=self.frames+1 if token==self.candidate else 1;self.candidate=token
        if self.frames<2:return False
        self.reason='볼 구매 화면 감지' if shop else f'리그 볼 부족 · 보유 {token[0]} / 필요 {token[1]}'
        return True
