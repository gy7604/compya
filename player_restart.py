"""Restart only the explicitly selected LDPlayer instance, with cancellable boot wait."""
from pathlib import Path
import subprocess
import time

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

    def restart(self):
        rows=self.command('list2').splitlines()
        if not any(row.split(',')[0].strip()==str(self.index) for row in rows):
            raise RuntimeError('지정한 LDPlayer 인스턴스를 찾지 못했습니다.')
        if self.stop.is_set():return False
        self.command('reboot','--index',str(self.index))
        if self.stop.wait(10):return False
        deadline=time.monotonic()+120
        while not self.stop.is_set() and time.monotonic()<deadline:
            try:
                self.adb.connect()
                if self.adb.run('shell','getprop','sys.boot_completed').strip()==b'1':return True
            except RuntimeError:pass
            if self.stop.wait(2):return False
        if self.stop.is_set():return False
        raise RuntimeError('LDPlayer 재시작 후 120초 안에 ADB 부팅 완료를 확인하지 못했습니다.')
