"""Outbound-only, narrowly scoped mobile control; never opens a PC listening port."""
import base64
import ctypes
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.request
import uuid
from version import VERSION
from pathlib import Path
from urllib.parse import urlsplit


def endpoint(value):
    url=urlsplit(value.strip())
    if (url.scheme!='https' or url.username or url.password or url.port or url.query or url.fragment
            or url.path not in ('','/') or not re.fullmatch(r'compya-mobile-control(?:-staging)?\.[a-z0-9-]+\.workers\.dev',url.hostname or '')):
        raise ValueError('등록된 모바일 제어용 HTTPS 주소를 입력하세요.')
    return 'https://'+url.hostname


def protected_bytes(data,decrypt=False):
    """Windows current-user DPAPI. No plaintext credential fallback."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    source=ctypes.create_string_buffer(data);incoming=Blob(len(data),ctypes.cast(source,ctypes.POINTER(ctypes.c_ubyte)));outgoing=Blob()
    operation=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    operation.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not operation(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(outgoing)):
        raise OSError('현재 Windows 사용자로 모바일 연결 정보를 보호/해제할 수 없습니다.')
    try:return ctypes.string_at(outgoing.data,outgoing.size)
    finally:kernel.LocalFree(outgoing.data)


class MobileStore:
    def __init__(self,root,protect=protected_bytes):
        self.path=Path(root)/'mobile_control.json';self.protect=protect

    def load(self):
        if not self.path.exists():return None
        data=json.loads(self.path.read_text(encoding='utf-8'))
        keys=json.loads(self.protect(base64.b64decode(data['protected']),True))
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{32,80}',keys.get(k,'')) for k in ('agent_key','owner_key')):
            raise ValueError('모바일 연결 정보가 잘못되었습니다.')
        return dict(url=endpoint(data['url']),enabled=data.get('enabled') is True,**keys)

    def save(self,config):
        url=endpoint(config['url'])
        sealed=self.protect(json.dumps({key:config[key] for key in ('agent_key','owner_key')}).encode())
        data=dict(url=url,enabled=bool(config['enabled']),protected=base64.b64encode(sealed).decode())
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2),encoding='utf-8');temp.replace(self.path)


def new_credentials():
    return dict(owner_key=secrets.token_urlsafe(24),agent_key=secrets.token_urlsafe(32))


def server_secrets(keys):
    return dict(OWNER_KEY_HASH=hashlib.sha256(keys['owner_key'].encode()).hexdigest(),
                AGENT_KEY_HASH=hashlib.sha256(keys['agent_key'].encode()).hexdigest(),SESSION_KEY=secrets.token_urlsafe(48))


class CommandJournal:
    """Persist before dispatch. An interrupted command is reported, never replayed."""
    def __init__(self,root):
        self.path=Path(root)/'mobile_commands.json';self.lock=threading.Lock();self.data={}
        if self.path.exists():self.data=json.loads(self.path.read_text(encoding='utf-8'))
        for item in self.data.values():
            if item['state']=='accepted':item.update(state='unknown',message='PC 재시작으로 결과 확인 불가 · 현재 상태를 확인하세요.')

    def save(self):
        self.data=dict(list(self.data.items())[-64:])
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8');temp.replace(self.path)

    def claim(self,command,boot_id,now):
        with self.lock:
            id_=command.get('id','')
            if not re.fullmatch(r'[a-f0-9-]{36}',id_) or command.get('action') not in ('start','stop','reboot','reboot_cancel'):
                return False
            if id_ in self.data:return False
            if command.get('boot_id')!=boot_id or not now<float(command.get('expires',0))<=now+35:return False
            self.data[id_]=dict(state='accepted',action=command['action'],message='PC에서 명령 확인 중')
            try:self.save()
            except Exception:
                self.data.pop(id_,None);raise
            return True

    def finish(self,id_,message,ok=True):
        with self.lock:
            self.data[id_].update(state='done' if ok else 'rejected',message=str(message)[:160]);self.save()

    def accepted(self,id_,action):
        with self.lock:return self.data.get(id_,{}).get('state')=='accepted' and self.data[id_]['action']==action

    def receipts(self):
        with self.lock:
            return [dict(id=key,**value) for key,value in self.data.items() if value['state']!='accepted'][-32:]


class MobileAgent:
    def __init__(self,root,config,dispatch,capture,notify=lambda value:None,opener=None):
        self.config=config;self.dispatch=dispatch;self.capture=capture;self.notify=notify
        self.journal=CommandJournal(root);self.boot_id=str(uuid.uuid4());self.shutdown=threading.Event();self.lock=threading.Lock()
        self.summary={};self.png=None;self.screen_at=0;self.last_screen=0;self.connected=None
        from mobile_records import RecordReader
        self.reader=RecordReader(root)
        if opener is None:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            opener=urllib.request.build_opener(NoRedirect()).open
        self.opener=opener;self.thread=threading.Thread(target=self.run,daemon=True,name='CompyaMobile')

    def start(self):self.thread.start()
    def close(self):self.shutdown.set()

    def publish(self,summary):
        with self.lock:self.summary=dict(summary)

    def game_frame(self,png):
        if not isinstance(png,bytes):return
        with self.lock:self.png=png;self.screen_at=time.time()

    def payload(self,want_screen=False):
        now=time.time()
        with self.lock:summary=dict(self.summary);png=self.png;captured=self.screen_at
        data=dict(boot_id=self.boot_id,status=summary,acks=self.journal.receipts(),telemetry=self.reader.snapshot())
        if want_screen and now-self.last_screen>=5:
            if not summary.get('running'):
                png=self.capture();captured=time.time() if png else 0
            if png and now-captured<12:
                import cv2
                import numpy as np
                frame=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
                if frame is not None and frame.shape[:2]==(1280,720):
                    scaled=cv2.resize(frame,(360,640));ok,jpg=cv2.imencode('.jpg',scaled,[cv2.IMWRITE_JPEG_QUALITY,65])
                    if ok and len(jpg)<180_000:
                        data.update(screen=base64.b64encode(jpg).decode(),screen_at=captured)
            self.last_screen=now
        return data

    def exchange(self,data):
        request=urllib.request.Request(self.config['url']+'/api/agent/sync',data=json.dumps(data).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+self.config['agent_key'],
                             'User-Agent':'CompyaMobile/'+VERSION},method='POST')
        with self.opener(request,timeout=6) as response:
            raw=response.read(4097)
            if len(raw)>4096:raise ValueError('모바일 서버 응답 제한 초과')
            return json.loads(raw)

    def run(self):
        want_screen=False
        while not self.shutdown.is_set():
            try:
                result=self.exchange(self.payload(want_screen));want_screen=result.get('viewing') is True
                if self.connected is not True:self.notify('모바일 연결됨')
                self.connected=True
                command=result.get('command')
                if command and not self.shutdown.is_set() and self.journal.claim(command,self.boot_id,time.time()):
                    self.dispatch(command)
            except Exception:
                if self.connected is not False:self.notify('모바일 연결 대기 · 매크로는 계속 실행됩니다.')
                self.connected=False;want_screen=False
            self.shutdown.wait(3 if want_screen else 10)
        # A deliberate program exit/connection disable should not look like a crash.
        try:
            data=self.payload(False);data['status'].update(closing=True,running=False,stopping=True)
            self.exchange(data)
        except Exception:pass
