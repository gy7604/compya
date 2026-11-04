"""Close LDPlayer's Windows ad surface, which Android screencap cannot see.

Only the exact ADFullScreenFrame owned by verified LDPlayer instance 0 is
eligible. No desktop pixels, guessed coordinates, global keys, or cursor moves.
"""
from pathlib import Path
import ctypes
from ctypes import wintypes as W
import subprocess
import time
from interruptions import PopupCloser
from player_restart import validate_serial
from engine import SafetyStop

AD_CLASS='ADFullScreenFrame'


class PlayerWindowClosed(RuntimeError):pass


def selected_row(output):
    rows=[line.split(',') for line in output.splitlines() if line.split(',')[0].strip()=='0']
    if len(rows)!=1 or len(rows[0])<6:raise RuntimeError('LDPlayer 0번 창을 확인하지 못했습니다.')
    try:handle,pid=int(rows[0][2]),int(rows[0][5])
    except ValueError:raise RuntimeError('LDPlayer 0번 창 정보가 올바르지 않습니다.') from None
    if handle<=0 or pid<=0:raise PlayerWindowClosed('LDPlayer 0번 실행 창이 없습니다.')
    return handle,pid


def is_player_ad(window,root,pid):
    """A visible ad must belong to the same process and owner, inside its frame."""
    if (window['handle']==root['handle'] or window['kind']!=AD_CLASS or
            window['pid']!=pid or window['owner']!=root['handle'] or not window['visible']):return False
    left,top,right,bottom=window['rect'];rl,rt,rr,rb=root['rect']
    return rl<=left<right<=rr and rt<=top<bottom<=rb


class NativeSurface:
    def __init__(self,console,serial):
        validate_serial(0,serial)
        self.console=Path(console);self.expected=(self.console.parent/'dnplayer.exe').resolve()
        self.user=ctypes.WinDLL('user32',use_last_error=True);self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.last_binding=float('-inf');self.binding=None
        signatures={
            'GetWindowRect':([W.HWND,ctypes.POINTER(W.RECT)],W.BOOL),
            'GetWindowThreadProcessId':([W.HWND,ctypes.POINTER(W.DWORD)],W.DWORD),
            'IsWindow':([W.HWND],W.BOOL),'IsIconic':([W.HWND],W.BOOL),
            'GetClassNameW':([W.HWND,W.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetAncestor':([W.HWND,W.UINT],W.HWND),'IsWindowVisible':([W.HWND],W.BOOL),
            'SetThreadDpiAwarenessContext':([ctypes.c_void_p],ctypes.c_void_p),
            'PostMessageW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],W.BOOL)}
        for name,(args,result) in signatures.items():
            function=getattr(self.user,name);function.argtypes=args;function.restype=result
        self.callback_type=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        self.user.EnumWindows.argtypes=[self.callback_type,W.LPARAM];self.user.EnumWindows.restype=W.BOOL
        self.kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];self.kernel.OpenProcess.restype=W.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)];self.kernel.QueryFullProcessImageNameW.restype=W.BOOL
        self.kernel.CloseHandle.argtypes=[W.HANDLE];self.kernel.CloseHandle.restype=W.BOOL

    def verify(self,handle,pid):
        actual=W.DWORD()
        if not self.user.IsWindow(handle) or not self.user.GetWindowThreadProcessId(handle,ctypes.byref(actual)) or actual.value!=pid:return False
        process=self.kernel.OpenProcess(0x1000,False,pid)
        if not process:return False
        try:
            buffer=ctypes.create_unicode_buffer(32768);length=W.DWORD(len(buffer))
            return bool(self.kernel.QueryFullProcessImageNameW(process,0,buffer,ctypes.byref(length))) and Path(buffer.value).resolve()==self.expected
        finally:self.kernel.CloseHandle(process)

    def resolve(self):
        if self.binding and time.monotonic()-self.last_binding<5 and self.verify(*self.binding):return self.binding
        result=subprocess.run([str(self.console),'list2'],capture_output=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError('LDPlayer 창 조회 실패')
        binding=selected_row(result.stdout.decode(errors='replace'))
        if not self.verify(*binding):raise RuntimeError('지정한 LDPlayer 실행 파일과 창의 소유자가 다릅니다.')
        self.binding=binding;self.last_binding=time.monotonic();return binding

    def describe(self,handle):
        actual=W.DWORD();self.user.GetWindowThreadProcessId(handle,ctypes.byref(actual))
        name=ctypes.create_unicode_buffer(256);self.user.GetClassNameW(handle,name,len(name))
        rect=W.RECT()
        if not self.user.GetWindowRect(handle,ctypes.byref(rect)):raise RuntimeError('플레이어 광고 창 위치 확인 실패')
        return dict(handle=int(handle),pid=actual.value,kind=name.value,rect=(rect.left,rect.top,rect.right,rect.bottom),
                    owner=int(self.user.GetAncestor(handle,3) or 0),visible=bool(self.user.IsWindowVisible(handle)))

    def capture(self):
        handle,pid=self.resolve()
        context=self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            root=self.describe(handle);ads=[];errors=[]
            @self.callback_type
            def collect(candidate,_):
                actual=W.DWORD();self.user.GetWindowThreadProcessId(candidate,ctypes.byref(actual))
                # Do not read other applications' titles, paths, or pixels.
                if actual.value==pid and self.user.IsWindowVisible(candidate):
                    try:
                        value=self.describe(candidate)
                        if is_player_ad(value,root,pid):ads.append(value['handle'])
                    except Exception:errors.append(True)
                return True
            if not self.user.EnumWindows(collect,0) or errors:raise RuntimeError('플레이어 광고 창 조회 실패')
            return dict(handle=handle,pid=pid,ads=tuple(ads),minimized=bool(self.user.IsIconic(handle)))
        finally:
            if context:self.user.SetThreadDpiAwarenessContext(context)

    def close(self,snapshot):
        # Re-enumerate: a stale handle or another instance must never receive
        # WM_CLOSE. The main/game window is never a recipient.
        current=self.capture()
        if (current['handle'],current['pid'])!=(snapshot['handle'],snapshot['pid']):return False
        if len(current['ads'])!=1 or current['ads']!=snapshot['ads']:return False
        target=current['ads'][0]
        if target==current['handle'] or not self.verify(target,current['pid']):return False
        return bool(self.user.PostMessageW(target,0x10,0,0))


class PlayerAds:
    def __init__(self,surface,stop):
        self.surface,self.stop=surface,stop
        self.guard=PopupCloser(['host-ad']);self.last=float('-inf');self.hold=False;self.resumed=False;self.events=[];self.warned=False;self.failures=0

    def observe(self,now,dry=False):
        self.resumed=False
        if now-self.last<1:return self.hold
        self.last=now
        try:snapshot=self.surface.capture()
        except PlayerWindowClosed:
            # A positively stopped instance must reach the existing ADB/player
            # recovery path, rather than remaining stuck behind an old ad hold.
            self.resumed=self.hold;self.hold=False;self.guard=PopupCloser(['host-ad']);self.failures=0
            return False
        except Exception:
            self.failures+=1
            if self.hold and self.failures>=3:raise SafetyStop('플레이어 광고 닫힘 확인 3회 실패 · 경기 입력 보류 상태에서 확인 필요')
            if not self.warned:self.events.append('플레이어 광고 창 확인 대기 · 등록된 게임 화면 감시는 유지합니다.');self.warned=True
            return self.hold
        self.warned=False;self.failures=0
        # Hiding/minimizing an already detected ad does not prove it closed.
        if snapshot['minimized'] and self.hold:return True
        present=bool(snapshot['ads']);previously=self.hold
        # Even manual closure during initial debounce requires three clean
        # observations; invisibility for a single poll cannot resume input.
        if present and self.guard.pending is None:self.guard.pending='host-ad'
        action=self.guard.observe({'host-ad':present},now)
        self.hold=self.guard.hold;self.resumed=self.guard.resumed
        if self.hold and not previously:self.events.append('LDPlayer 외부 광고 감지 · 경기 입력 보류')
        if action and len(snapshot['ads'])==1 and not self.stop.is_set():
            if dry:self.events.append('모의 플레이어 광고 닫기 · 실제 클릭 안 함')
            elif self.surface.close(snapshot):self.events.append('LDPlayer 외부 광고 닫기 · 사라짐 확인 중')
        if self.resumed:self.events.append('LDPlayer 외부 광고 닫힘 확인 · 경기 입력 재개')
        return self.hold


def create_player_ads(config,stop):
    import sys
    if sys.platform!='win32':return None
    recovery=config.get('recovery',{})
    if recovery.get('player_index',0)!=0:return None
    console=recovery.get('player_console') or str(Path(config['adb']).resolve().with_name('ldconsole.exe'))
    if not Path(console).is_file():return None
    return PlayerAds(NativeSurface(console,config['serial']),stop)
