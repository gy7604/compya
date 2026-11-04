"""Poll ball stock every five minutes; confirm five balls after 1.5 seconds."""
import json
import math
from datetime import datetime,timedelta,timezone
from pathlib import Path

KST=timezone(timedelta(hours=9))


class BallRest:
    interval=300
    confirm_delay=1.5

    def __init__(self,root,dry=False,clock=None):
        self.path=Path(root)/'ball_rest.json';self.dry=dry
        self.clock=clock or (lambda:datetime.now(KST));self.waiting=False;self.next_check=None;self.events=[]
        self.last_stock=None;self.candidate=None;self.candidate_at=None
        self.last_minute=None;self.next_close=float('-inf');self.close_counts={}
        if not dry and self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('schema')==2:
                self.waiting=data.get('waiting') is True;self.last_stock=data.get('last_stock')
                if self.waiting:
                    self.next_check=datetime.fromisoformat(data['next_check_at'])
                    if self.next_check.tzinfo is None:raise ValueError('볼 잔량 확인 시간대를 확인하세요.')
            elif data.get('until'):
                self.waiting=True;self.next_check=self.clock()
                self.events.append('기존 볼 충전 대기 이어받기 · 남은 볼을 직접 확인한 뒤 재개')

    def save(self):
        if self.dry:return
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(schema=2,waiting=self.waiting,last_stock=self.last_stock,
                                       next_check_at=self.next_check.isoformat() if self.next_check else None)),encoding='utf-8')
        temp.replace(self.path)

    @property
    def active(self):return self.waiting

    def begin(self,reason):
        if self.active:return False
        self.waiting=True;self.next_check=self.clock();self.candidate=None;self.candidate_at=None;self.save()
        self.last_minute=None;self.close_counts={};self.next_close=float('-inf')
        self.events.append(reason+' · 볼 충전 대기 · 5분마다 잔량 확인 · 5개 이상 두 번 확인 후 재개')
        return True

    def due(self):return self.active and self.clock()>=self.next_check

    def tick(self):
        if not self.active:return
        minute=math.ceil(max(0,(self.next_check-self.clock()).total_seconds())/60)
        status=('confirm',self.candidate) if self.candidate else ('poll',minute)
        if status!=self.last_minute:
            self.events.append(f'볼 잔량 {self.candidate[0]}개 · 즉시 재확인 중' if self.candidate else
                               '볼 충전 대기 · '+(f'{minute}분 후 잔량 재확인' if minute else '로비의 볼 잔량 확인 대기'))
            self.last_minute=status

    def submit(self,stock):
        if not self.due():return False
        now=self.clock()
        valid=(isinstance(stock,tuple) and len(stock)==2 and all(type(x) is int for x in stock)
               and 0<=stock[0]<=9999 and 50<=stock[1]<=9999)
        if not valid:
            self.candidate=None;self.candidate_at=None;self.next_check=now+timedelta(seconds=self.interval)
            self.events.append('볼 잔량 판독 불확실 · 대기 유지 · 5분 뒤 재확인')
        else:
            self.last_stock=list(stock)
            if stock[0]<5:
                self.candidate=None;self.candidate_at=None;self.next_check=now+timedelta(seconds=self.interval)
                self.events.append(f'볼 잔량 {stock[0]}/{stock[1]} · 5개 미만 · 5분 뒤 재확인')
            elif self.candidate==stock and self.confirm_delay<=(now-self.candidate_at).total_seconds()<=30:
                self.waiting=False;self.next_check=None;self.candidate=None;self.candidate_at=None;self.save()
                self.events.append(f'볼 잔량 {stock[0]}개 두 번 일치 · 리그 자동 재개');return True
            else:
                self.candidate=stock;self.candidate_at=now;self.next_check=now+timedelta(seconds=self.confirm_delay)
                self.events.append(f'볼 잔량 {stock[0]}개 확인 · 1.5초 뒤 즉시 재확인')
        self.last_minute=None;self.save();return False

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
        app.dispatch_notification(config,'컴프야: '+guard.reason+'. 리그를 일시 정지하고 5분마다 남은 볼을 확인합니다. 5개 이상을 두 번 확인하면 자동으로 재개합니다.')
