"""Explicitly confirmed Windows restart; never forces unsaved applications closed."""
import os
from pathlib import Path
import subprocess
import threading


def restart_pc(runner=None):
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/shutdown.exe'
    # A positive Windows /t implies /f. Wait inside this app instead, then /t 0
    # so unsaved work is never forcibly discarded by an implicit force option.
    args=[str(executable),'/r','/t','0','/d','p:0:0','/c','Compya mobile restart']
    result=(runner or subprocess.run)(args,capture_output=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Windows 재부팅 요청 실패')


class RebootTimer:
    def __init__(self,done,action=restart_pc):
        self.done=done;self.action=action;self.cancelled=threading.Event();self.lock=threading.Lock();self.issued=False

    def start(self):threading.Thread(target=self.run,daemon=True,name='CompyaRestart').start()

    def run(self):
        if self.cancelled.wait(20):return
        with self.lock:
            if self.cancelled.is_set():return
            self.issued=True
        try:self.action();self.done(True)
        except Exception:self.done(False)

    def cancel(self):
        with self.lock:
            if self.issued:return False
            self.cancelled.set();return True
