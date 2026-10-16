import json
import re
import urllib.request
from pathlib import Path


def default_notifications():
    return dict(enabled=False, watch=[], topic='', message='게임에서 확인이 필요한 화면이 나타났습니다.', stop=False)


def validate_notifications(config, records):
    if not config.get('enabled'): return
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', config.get('topic','')):
        raise ValueError('알림 채널은 영문·숫자·밑줄·하이픈 20~128자로 설정하세요.')
    available={r['id'] for r in records if r['enabled'] and r['template']}
    if not config.get('watch') or not set(config['watch']) <= available:
        raise ValueError('알림 이미지를 선택하고 버튼 관리에서 이미지 등록과 사용을 켜세요.')
    if not config.get('message','').strip() or len(config['message'])>1000:
        raise ValueError('알림 문구는 1~1000자로 입력하세요.')


class NotificationStore:
    def __init__(self,root): self.path=Path(root)/'notifications.json'
    def load(self):
        value=default_notifications()
        if self.path.exists(): value.update(json.loads(self.path.read_text(encoding='utf-8')))
        return value
    def save(self,config,records):
        validate_notifications(config,records)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(self.path)


def send_notification(config, text, opener=None):
    # Fixed HTTPS host; never accept a user-supplied URL or redirect target.
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,128}',config.get('topic','')):
        raise ValueError('알림 채널을 확인하세요.')
    request=urllib.request.Request('https://ntfy.sh/'+config['topic'],data=text.encode('utf-8'),
        headers={'Content-Type':'text/plain; charset=utf-8','Title':'V26 Macro','Priority':'default'},method='POST')
    if opener is None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=urllib.request.build_opener(NoRedirect()).open
    with opener(request,timeout=8) as response:
        if not 200 <= response.status < 300: raise RuntimeError('알림 서버 응답 오류')


class NotificationDetector:
    def __init__(self,config,records):
        validate_notifications(config,records)
        self.config=config
        self.present={};self.absent={};self.latched=set()
    def observe(self,found):
        hits=[]
        if not self.config['enabled']: return hits
        for key in self.config['watch']:
            if found.get(key,False):
                self.absent[key]=0
                self.present[key]=self.present.get(key,0)+1
                if self.present[key]>=3 and key not in self.latched:
                    self.latched.add(key);hits.append(key)
            else:
                self.present[key]=0
                self.absent[key]=self.absent.get(key,0)+1
                if self.absent[key]>=3:self.latched.discard(key)
        return hits
