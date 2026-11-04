"""Current-user logon startup and explicit last-run intent; no Windows auto-login."""
import ctypes
import json
import threading
from pathlib import Path

RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME='CompyaMobile'


class ResumeStore:
    def __init__(self,root):
        self.path=Path(root)/'startup_restore.json';self.lock=threading.RLock()
        self.data=dict(enabled=False,desired_running=False,confirmed=False,dry=True,serial='')
        if self.path.exists():
            value=json.loads(self.path.read_text(encoding='utf-8'))
            for key in ('enabled','desired_running','confirmed','dry'):
                if type(value.get(key)) is bool:self.data[key]=value[key]
            self.data['serial']=str(value.get('serial',''))

    def update(self,**values):
        with self.lock:
            data=dict(self.data,**values);temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(data,indent=2),encoding='utf-8');temp.replace(self.path);self.data=data

    def should_resume(self,serial):
        with self.lock:
            return (self.data['enabled'] and self.data['desired_running'] and self.data['confirmed']
                    and serial==self.data['serial']=='127.0.0.1:5555')


def startup_command(executable):
    path=Path(executable).resolve()
    if path.name!='V26Macro.exe' or not path.is_file():raise ValueError('설치된 V26Macro.exe가 필요합니다.')
    command=f'"{path}" --restore-session'
    if len(command)>260:raise ValueError('프로그램 경로가 너무 깁니다.')
    return command


def set_startup(executable,enabled,registry=None):
    if registry is None:import winreg as registry
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,RUN_KEY,0,registry.KEY_SET_VALUE) as key:
        if enabled:registry.SetValueEx(key,RUN_NAME,0,registry.REG_SZ,startup_command(executable))
        else:
            try:registry.DeleteValue(key,RUN_NAME)
            except FileNotFoundError:pass


def single_instance():
    """Keep the same device from being driven by both a logon and desktop launch."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];kernel.CreateMutexW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateMutexW(None,False,r'Local\CompyaMobileMacroV1')
    if not handle:raise OSError('중복 실행 상태 확인 실패')
    if ctypes.get_last_error()==183:kernel.CloseHandle(handle);return None
    import atexit
    atexit.register(lambda:kernel.CloseHandle(handle))
    return handle
