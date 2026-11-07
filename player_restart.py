"""Restart only the explicitly selected LDPlayer instance, with cancellable boot wait."""
from pathlib import Path
import subprocess
import time
from engine import SafetyStop

def validate_serial(index, serial):
    allowed={f'emulator-{5554+2*index}',f'127.0.0.1:{5555+2*index}',f'localhost:{5555+2*index}'}
    if serial not in allowed:raise ValueError('LDPlayer 번호와 ADB 장치 주소가 다릅니다. 재시작 대상 번호를 확인하세요.')

class PlayerRestart:
    def __init__(self, console, index, adb, stop):
        validate_serial(index,adb.serial)
        self.console=Path(console) if console else Path(adb.executable).resolve().with_name('ldconsole.exe')
        if not self.console.is_file():raise ValueError('LDPlayer ldconsole.exe 경로를 확인하세요.')
        self.index,self.adb,self.stop=index,adb,stop

    def command(self,*args):
        result=subprocess.run([str(self.console),*args],capture_output=True,timeout=15,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError('LDPlayer 제어 명령 실패')
        return result.stdout.decode(errors='replace')

    def connect_selected(self):
        """Reconnect only the configured instance, never pick another device."""
        try:
            self.adb.connect()
        except RuntimeError:
            original=self.adb.serial
            fallback=f'127.0.0.1:{5555+2*self.index}'
            if original==fallback:raise
            self.adb.serial=fallback
            try:self.adb.connect()
            except RuntimeError:
                self.adb.serial=original
                raise

    def startup_state(self):
        rows=[line.split(',') for line in self.command('list2').splitlines()
              if line.split(',')[0].strip()==str(self.index)]
        if len(rows)!=1 or len(rows[0])<7:raise SafetyStop('시작 준비 · 지정한 LDPlayer 실행 정보 확인 실패 · 추가 실행 안 함')
        try:started,frontend,vm=(int(rows[0][i]) for i in (4,5,6))
        except ValueError:raise SafetyStop('시작 준비 · LDPlayer 상태 확인 실패 · 추가 실행 안 함') from None
        if started not in (0,1,2):raise SafetyStop('시작 준비 · 알 수 없는 LDPlayer 상태 · 추가 실행 안 함')
        # Do not let the ordinary boot-retry path launch over a closing VM.
        if started==0 and (frontend>0 or vm>0):
            raise SafetyStop('시작 준비 · 이전 LDPlayer 0번 종료 미확인 · 중복 실행 없이 확인 필요')
        return started!=0

    def ensure_started(self):
        """Start only a stopped instance 0; healthy/booting instances stay intact."""
        if self.index!=0:raise SafetyStop('시작 자동 실행은 지정한 LDPlayer 0번만 지원합니다.')
        self.started_on_demand=False
        if self.stop.is_set():return False
        if not self.startup_state() and not self.startup_state():
            if self.stop.is_set():return False
            self.command('launch','--index','0');self.started_on_demand=True
            if self.stop.wait(10):return False
        return self.wait_boot()

    def wait_boot(self):
        deadline=time.monotonic()+120
        for _ in range(60):
            if self.stop.is_set():return False
            if time.monotonic()>=deadline:break
            try:
                self.connect_selected()
                if self.adb.run('shell','getprop','sys.boot_completed').strip()==b'1':return True
            except RuntimeError:pass
            if self.stop.wait(2):return False
        if self.stop.is_set():return False
        raise RuntimeError('LDPlayer 준비 후 120초 안에 ADB 부팅 완료를 확인하지 못했습니다.')

    def restart(self,full=False):
        rows=self.command('list2').splitlines()
        row=next((row.split(',') for row in rows if row.split(',')[0].strip()==str(self.index)),None)
        if row is None:
            raise RuntimeError('지정한 LDPlayer 인스턴스를 찾지 못했습니다.')
        if self.stop.is_set():return False
        if full and len(row)>4 and row[4].strip()!='0':
            # A VM reboot can preserve a broken host-side ADB/NAT process.
            # Quit only this instance before re-launching; never kill the
            # shared ADB server or another emulator's process.
            self.command('quit','--index',str(self.index))
            closed=False
            quit_deadline=time.monotonic()+30
            for _ in range(30):
                if time.monotonic()>=quit_deadline:break
                if self.stop.wait(1):return False
                current=next((line.split(',') for line in self.command('list2').splitlines() if line.split(',')[0].strip()==str(self.index)),None)
                if current is None or (len(current)>4 and current[4].strip()=='0'):
                    closed=True;break
            if not closed:raise RuntimeError('지정한 LDPlayer 종료 확인 실패 · 중복 실행 없이 중지')
            row=['','','','','0']
        action='launch' if len(row)>4 and row[4].strip()=='0' else 'reboot'
        self.command(action,'--index',str(self.index))
        if self.stop.wait(10):return False
        return self.wait_boot()
