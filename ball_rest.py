"""A durable 26 minute league pause; manual Stop always remains responsive."""
import json
import math
from datetime import datetime,timedelta,timezone
from pathlib import Path

KST=timezone(timedelta(hours=9))


class BallRest:
    seconds=26*60

    def __init__(self,root,dry=False,clock=None):
        self.path=Path(root)/'ball_rest.json';self.dry=dry
        self.clock=clock or (lambda:datetime.now(KST));self.until=None;self.events=[]
        self.last_minute=None;self.next_close=float('-inf');self.close_counts={}
        if not dry and self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('until'):
                self.until=datetime.fromisoformat(data['until'])
                if self.until.tzinfo is None:raise ValueError('볼 충전 대기 시간대를 확인하세요.')

    def save(self):
        if self.dry:return
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'until':self.until.isoformat() if self.until else None}),encoding='utf-8')
        temp.replace(self.path)

    @property
    def active(self):return self.until is not None

    def begin(self,reason):
        if self.active:return False
        self.until=self.clock()+timedelta(seconds=self.seconds);self.save()
        self.last_minute=None;self.close_counts={};self.next_close=float('-inf')
        self.events.append(reason+' · 볼 충전 26분 대기 · '+self.until.astimezone(KST).strftime('%H:%M:%S')+' 리그 재개 예정')
        return True

    def tick(self):
        if not self.active:return False
        remaining=max(0,math.ceil((self.until-self.clock()).total_seconds()))
        if remaining==0:
            self.until=None;self.save();self.events.append('볼 충전 대기 26분 완료 · 현재 화면에서 리그 재개');return True
        minute=math.ceil(remaining/60)
        if minute!=self.last_minute:
            self.events.append(f'볼 충전 대기 · 남은 {minute}분 · 리그 자동 재개 예정')
            self.last_minute=minute
        return False

    def close_point(self,layout,now):
        # Leave only positively recognized free overlays/result menus.
        points={'detail':(360,1222),'info':(610,287),'result':(515,1177)}
        if layout not in points or now<self.next_close:return None
        count=self.close_counts.get(layout,0)
        if count>=3:return None
        self.close_counts[layout]=count+1;self.next_close=now+5
        return points[layout]


def begin_ball_rest(app,cfg,guard,rest,png,root):
    if not rest.begin(guard.reason):return
    while rest.events:app.emit(rest.events.pop(0))
    try:
        folder=Path(root)/'logs';folder.mkdir(exist_ok=True);(folder/'ball_wait.png').write_bytes(png)
    except OSError:app.emit('볼 충전 대기 화면 저장 실패 · 대기는 유지')
    config=cfg.get('notifications',{})
    if cfg['dry']:app.emit('모의 볼 충전 대기 알림 · 실제 전송 안 함')
    elif config.get('enabled'):
        app.dispatch_notification(config,'컴프야: '+guard.reason+'. 볼 충전을 위해 26분간 리그를 일시 정지합니다. '+rest.until.astimezone(KST).strftime('%H:%M:%S')+'부터 자동으로 재개합니다.')
