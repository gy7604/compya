"""Observe only LDPlayer 0; recycle by normal quit, never trim or kill processes."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import time
from engine import SafetyStop

GIB=1024**3


def selected_vm(output):
    rows=[line.split(',') for line in output.splitlines() if line.split(',')[0].strip()=='0']
    if len(rows)!=1 or len(rows[0])<7:raise RuntimeError('메모리 관리 · 0번 실행 정보 확인 실패')
    row=rows[0]
    try:pid=int(row[6]);started=int(row[4])
    except ValueError:raise RuntimeError('메모리 관리 · 0번 프로세스 정보 확인 실패') from None
    return started,pid


class MemoryCounters(ctypes.Structure):
    _fields_=[('cb',W.DWORD),('PageFaultCount',W.DWORD)]+[(name,ctypes.c_size_t) for name in
        ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
         'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]


class Performance(ctypes.Structure):
    _fields_=[('cb',W.DWORD)]+[(name,ctypes.c_size_t) for name in
        ('CommitTotal','CommitLimit','CommitPeak','PhysicalTotal','PhysicalAvailable','SystemCache',
         'KernelTotal','KernelPaged','KernelNonpaged','PageSize')]+[(name,W.DWORD) for name in
        ('HandleCount','ProcessCount','ThreadCount')]


class ProcessLease:
    def __init__(self,api,handle,pid,created):self.api,self.handle,self.pid,self.created=api,handle,pid,created
    def alive(self):
        result=self.api.kernel.WaitForSingleObject(self.handle,0)
        if result==0:return False
        if result==258:return True
        raise RuntimeError('메모리 관리 · 이전 0번 종료 상태 확인 실패')
    def close(self):
        if self.handle:self.api.kernel.CloseHandle(self.handle);self.handle=None


class WindowsMemory:
    def __init__(self,player):
        if player.index!=0:raise ValueError('메모리 관리는 지정된 LDPlayer 0번만 지원합니다.')
        self.player=player;self.retiring=None
        self.expected=(Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'ldplayer9box'/'Ld9BoxHeadless.exe').resolve()
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.psapi=ctypes.WinDLL('psapi',use_last_error=True)
        signatures={'OpenProcess':([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
            'CloseHandle':([W.HANDLE],W.BOOL),'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD),
            'QueryFullProcessImageNameW':([W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)],W.BOOL),
            'GetProcessTimes':([W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4,W.BOOL)}
        for name,(args,result) in signatures.items():
            f=getattr(self.kernel,name);f.argtypes=args;f.restype=result
        self.psapi.GetProcessMemoryInfo.argtypes=[W.HANDLE,ctypes.POINTER(MemoryCounters),W.DWORD];self.psapi.GetProcessMemoryInfo.restype=W.BOOL
        self.psapi.GetPerformanceInfo.argtypes=[ctypes.POINTER(Performance),W.DWORD];self.psapi.GetPerformanceInfo.restype=W.BOOL
    def row(self):return selected_vm(self.player.command('list2'))
    def open_current(self):
        started,pid=self.row()
        if started==0 and pid<=0:return None
        if pid<=0:raise RuntimeError('메모리 관리 · 플레이어 부팅 중')
        handle=self.kernel.OpenProcess(0x1000|0x100000,False,pid)
        if not handle:raise RuntimeError('메모리 관리 · 0번 메모리 조회 권한 확인 실패')
        try:
            path=ctypes.create_unicode_buffer(32768);length=W.DWORD(len(path))
            if not self.kernel.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(length)) or Path(path.value).resolve()!=self.expected:
                raise RuntimeError('메모리 관리 · 0번 실행 파일 검증 실패')
            times=[W.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):raise RuntimeError('메모리 관리 · 프로세스 생성 시각 확인 실패')
            if self.row()[1]!=pid:raise RuntimeError('메모리 관리 · 조회 중 0번 프로세스 변경')
            created=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
            return ProcessLease(self,handle,pid,created)
        except Exception:self.kernel.CloseHandle(handle);raise
    def read(self):
        lease=self.open_current()
        if lease is None:raise RuntimeError('메모리 관리 · 0번 실행 대기')
        try:
            counters=MemoryCounters();counters.cb=ctypes.sizeof(counters)
            perf=Performance();perf.cb=ctypes.sizeof(perf)
            if not self.psapi.GetProcessMemoryInfo(lease.handle,ctypes.byref(counters),counters.cb):raise RuntimeError('0번 메모리 조회 실패')
            if not self.psapi.GetPerformanceInfo(ctypes.byref(perf),perf.cb) or not perf.CommitLimit:raise RuntimeError('시스템 메모리 조회 실패')
            return dict(identity=[lease.pid,lease.created],private_bytes=int(counters.PrivateUsage),
                        commit_percent=100*perf.CommitTotal/perf.CommitLimit)
        finally:lease.close()
    def recycle(self):
        """Keep the old process handle across retries, including list2's early zero."""
        if self.player.stop.is_set():return False
        if self.retiring is None:self.retiring=self.open_current()
        lease=self.retiring
        if lease is not None:
            _,current_pid=self.row()
            if current_pid>0 and current_pid!=lease.pid:raise RuntimeError('메모리 정리 중 다른 0번 실행 감지 · 재시작 보류')
            self.player.command('quit','--index','0')
            deadline=time.monotonic()+30
            for _ in range(30):
                if self.player.stop.wait(1):return False
                started,current_pid=self.row()
                if current_pid>0 and current_pid!=lease.pid:raise RuntimeError('메모리 정리 중 0번 프로세스 변경')
                if not lease.alive() and started==0 and current_pid<=0:break
                if time.monotonic()>=deadline:raise RuntimeError('0번 프로세스 종료 미확인 · 추가 실행 없이 대기')
            else:raise RuntimeError('0번 프로세스 종료 미확인 · 추가 실행 없이 대기')
            lease.close();self.retiring=None
        if self.player.stop.is_set():return False
        return self.player.restart()
    def close(self):
        if self.retiring:self.retiring.close();self.retiring=None


class MemoryPolicy:
    def __init__(self,path,limit_gb=12):
        if type(limit_gb) is not int or not 8<=limit_gb<=32:raise ValueError('메모리 정리 기준은 8~32GB입니다.')
        self.path=Path(path);self.limit=limit_gb*GIB;self.hard=max(16,limit_gb+4)*GIB
        self.high=0;self.pending=False;self.urgent=False;self.manual=False;self.sample=None
        self.last_attempt=None
        if self.path.exists():
            value=json.loads(self.path.read_text(encoding='utf8')).get('last_attempt')
            if value is not None and (type(value) not in (int,float) or not 0<=value<1e12):raise ValueError('메모리 정리 기록 확인 실패')
            self.last_attempt=value
    def observe(self,sample):
        private=sample['private_bytes'];commit=sample['commit_percent']
        if type(private) is not int or private<0 or not isinstance(commit,(int,float)) or not 0<=commit<=100:raise ValueError('메모리 수치 확인 실패')
        if self.sample and sample['identity']!=self.sample['identity']:
            self.high=0;self.pending=self.manual
        self.sample=sample;self.high=self.high+1 if private>=self.limit else 0
        self.urgent=private>=self.hard or (commit>=90 and private>=8*GIB)
        self.pending=self.pending or self.high>=2 or self.urgent
    def request(self):self.manual=self.pending=True
    def due(self,safe,now):
        if not self.pending or not (safe or self.urgent):return False
        if self.last_attempt is not None and now-self.last_attempt<600:
            if self.urgent:raise SafetyStop('메모리 정리 후 10분 안에 다시 긴급 부족 · 반복 재시작 없이 확인 필요')
            return False
        return True
    def reserve(self,now):
        if not self.due(True,now):raise SafetyStop('메모리 정리 간격 확인 필요')
        self.last_attempt=now;self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(dict(last_attempt=now)),encoding='utf8');tmp.replace(self.path)
    def completed(self,sample):
        if self.sample and sample['identity']==self.sample['identity']:raise RuntimeError('메모리 정리 후 새 0번 프로세스를 확인하지 못했습니다.')
        if sample['private_bytes']>=self.limit or sample['commit_percent']>=90:raise RuntimeError('재실행 후에도 메모리 부족 · 추가 경기 입력 보류')
        self.observe(sample)
        self.sample=sample;self.pending=self.urgent=self.manual=False;self.high=0


class MemoryGuard:
    def __init__(self,probe,policy):
        self.probe,self.policy=probe,policy;self.last=float('-inf');self.events=[];self.warned=False;self.changed=False
    def poll(self,now):
        self.changed=False
        if now-self.last<30:return
        self.last=now;self.changed=True
        try:
            before=self.policy.pending;self.policy.observe(self.probe.read());self.warned=False
            if self.policy.pending and not before:self.events.append('플레이어 메모리 증가 · 경기 종료 후 정리 예약')
        except (RuntimeError,OSError) as error:
            if not self.warned:self.events.append('메모리 감시 대기 · '+str(error));self.warned=True
    def text(self):
        sample=self.policy.sample
        if not sample:return '플레이어 메모리 · 0번 조회 대기'
        state='조회 실패 · 이전 수치' if self.warned else '긴급 정리 필요' if self.policy.urgent else '경기 종료 후 정리 대기' if self.policy.pending else '감시 중'
        return f'플레이어 메모리 · {sample["private_bytes"]/GIB:.1f}GB / 정리 기준 {self.policy.limit/GIB:g}GB · 시스템 {sample["commit_percent"]:.0f}% · {state}'
    def recycle(self):
        if not self.probe.recycle():return False
        self.policy.completed(self.probe.read());return True
    def close(self):self.probe.close()


def create_memory_guard(config,player,root,dry=False):
    if dry or not config.get('memory_enabled',True) or player is None:return None
    if os.name!='nt' or player.index!=0:return None
    return MemoryGuard(WindowsMemory(player),MemoryPolicy(Path(root)/'player_memory_state.json',config.get('memory_limit_gb',12)))


def safe_boundary(count_enabled,counter,ranking,results,daily=None):
    """Never interrupt an unconfirmed purchase, reward or active ranking batch."""
    if count_enabled:return counter.result_frames>=counter.frames
    if not ranking.config['enabled'] or ranking.phase not in ('lobby','series'):return False
    lobby=ranking.config['lobby']
    if not results.get(lobby,(False,))[0] or ranking.counts.get(lobby,0)<3:return False
    entry=daily.entry if daily else {}
    return not any(entry.get(key) for key in ('pending','active_batch','reward_pending','result_pending'))


def run_cleanup(guard,recovery,adb,stop,emit):
    """Bound each preventive cycle separately from the session fault budget."""
    attempts=recovery.config['max_launches']
    for attempt in range(1,attempts+1):
        if stop.is_set():return False
        emit(f'메모리 정리 · 0번 정상 종료/부팅 확인 · {attempt}/{attempts}회')
        try:
            if not guard.recycle():return False
            if stop.is_set():return False
            adb.launch_app(recovery.config['package'])
            return True
        except (RuntimeError,OSError) as error:
            emit(f'메모리 정리 재접속 실패 · {attempt}/{attempts}회 · {error}')
            if attempt==attempts:
                # Existing player-failure telemetry/phone notification uses this
                # exhausted count. Successful maintenance never changes it.
                recovery.launches=attempts;recovery.active=True
                raise SafetyStop('메모리 정리 재접속 한도 도달 · 플레이어 확인 필요') from error
            if stop.wait(30):return False
    return False
