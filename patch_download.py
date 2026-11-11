"""One-shot, mode-independent game patch consent and bounded download hold.

Only the captured additional-data title AND blue confirm button authorize a tap.
Disappearance is not completion: require a registered return/gameplay screen.
"""
from pathlib import Path
import cv2
from engine import SafetyStop
from vision import Matcher,decode_screen

FILES=('patch_download_title.png','patch_download_confirm.png','patch_complete_title.png','patch_complete_start.png')
SUPPORTED=((480,854),(720,1280))

class PatchDownload:
    def __init__(self,root,timeout=1800):
        self.root=Path(root);self.title=self.confirm=None
        self.timeout=timeout;self.hold=False;self.checked=False;self.finished=False
        self.since=None;self.confirmed=False;self.started=False;self.frames=0;self.ready_frames=0
        self.candidate=None;self.last_action=None
        self.resume_key=None;self.events=[];self.elapsed=0;self.last_size=None

    def load(self):
        if self.title is not None:return
        self.title=Matcher(self.root/'assets'/FILES[0],.90,(166,278,160,50))
        self.confirm=Matcher(self.root/'assets'/FILES[1],.90,(238,505,162,65),color_tolerance=25)
        self.complete=Matcher(self.root/'assets'/FILES[2],.90,(130,750,235,40))
        self.start=Matcher(self.root/'assets'/FILES[3],.90,(75,778,330,65),color_tolerance=25)

    def scan(self,png):
        if not isinstance(png,bytes):return False,False,None,None
        self.load()
        im=decode_screen(png,color=True)
        if im is None:raise SafetyStop('패치 확인 화면 해독 실패')
        h,w=im.shape[:2];size=(w,h)
        if size not in SUPPORTED:return False,False,None,size
        if size!=(480,854):im=cv2.resize(im,(480,854),interpolation=cv2.INTER_AREA)
        normalized=cv2.imencode('.png',im)[1].tobytes()
        title=self.title.find(normalized)[0]
        complete=self.complete.find(normalized)[0]
        if title and complete:raise SafetyStop('패치 확인/완료 화면 중복 인식 · 클릭 없이 중지')
        title='complete' if complete else ('download' if title else None)
        button,center,_,_=(self.start if complete else self.confirm).find(normalized)
        point=(round(center[0]*w/480),round(center[1]*h/854))
        return title,button,point,size

    def observe(self,png,now,stop,resume=None,expected_sizes=()):
        self.finished=False
        if stop.is_set():return None
        title,button,point,size=self.scan(png);self.checked=True
        if title and self.since is None:
            if self.confirmed or self.started:raise SafetyStop('추가 다운로드 창 재등장 · 중복 확인 없이 중지')
            self.since=now;self.hold=True
            self.events.append(('추가 다운로드 완료 화면' if title=='complete' else '추가 다운로드 확인 창')+' 감지 · 일반 입력과 자동 재시작 보류')
        if self.since is None:return None
        self.hold=True
        if now-self.since>=self.timeout:
            raise SafetyStop('패치 다운로드 대기 30분 초과 · 추가 클릭/재시작 없이 중지')
        # Unsupported/changing resolution can never release the hold or tap.
        if size not in SUPPORTED or (self.last_size is not None and size!=self.last_size):
            self.frames=self.ready_frames=0;self.resume_key=self.candidate=None
            self.last_size=size
            return None
        self.last_size=size
        if title:
            self.ready_frames=0;self.resume_key=None
            self.frames=self.frames+1 if button and title==self.candidate else (1 if button else 0)
            self.candidate=title
            sent=self.started if title=='complete' else self.confirmed
            if not sent and self.frames>=3:
                if any(tuple(value)!=size for value in expected_sizes):
                    raise SafetyStop('패치 확인 화면과 등록 해상도가 다릅니다 · 클릭 없이 중지')
                if title=='complete':
                    self.started=True;self.last_action='patch_complete_start'
                    self.events.append('추가 다운로드 완료 확인 · 게임시작 1회 · 등록 복귀 화면 대기')
                else:
                    if self.started:raise SafetyStop('게임시작 후 다운로드 확인 재등장 · 클릭 없이 중지')
                    self.confirmed=True;self.last_action='patch_download_confirm'
                    self.events.append('추가 다운로드 확인 1회 · 완료 화면 확인까지 대기')
                return point
            return None
        self.frames=0;self.candidate=None
        # A callback keeps registered ROI scans off the patch/rotation frames.
        key=resume(size) if (self.confirmed or self.started) and resume else None
        self.ready_frames=self.ready_frames+1 if key and key==self.resume_key else (1 if key else 0)
        self.resume_key=key
        if self.ready_frames>=3:
            self.elapsed=now-self.since;self.since=None;self.hold=False;self.finished=True
            self.events.append('패치 후 등록 화면 3회 확인 · 매크로 재개')
        return None


def registered_popup_return(png,records,matchers,keys):
    """Yield only to validated, explicitly configured recovery popups.

    A generic footer phrase is insufficient: its independently recognized close
    circle and dark X must also be present before normal popup handling resumes.
    """
    from popup_appearance import footer_ad,read_appearances
    for key in keys:
        record=records.get(key)
        if not record or record.get('click_mode')!='fixed' or key not in matchers:continue
        if not matchers[key].find(png)[0]:continue
        if footer_ad(record) and key not in read_appearances(png,records,{key:True}):continue
        return key
    return None
