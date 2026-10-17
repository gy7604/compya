import subprocess


class ADB:
    def __init__(self, executable, serial, timeout=5):
        if not serial.strip():
            raise ValueError('ADB 장치 주소를 입력하세요.')
        self.executable, self.serial, self.timeout = executable, serial.strip(), timeout

    def run(self, *args, device=True):
        cmd = [self.executable] + (['-s', self.serial] if device else []) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=self.timeout,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f'ADB 실행/연결 실패: {e}') from e
        if p.returncode:
            raise RuntimeError('ADB 오류: ' + p.stderr.decode(errors='replace')[:300])
        return p.stdout

    def connect(self):
        if ':' in self.serial:
            self.run('connect', self.serial, device=False)
        if self.run('get-state').strip() != b'device':
            raise RuntimeError('ADB 장치가 준비되지 않았습니다.')

    def screenshot(self):
        return self.run('exec-out', 'screencap', '-p')

    def tap(self, point):
        self.run('shell', 'input', 'tap', str(point[0]), str(point[1]))


    def app_running(self,package):
        from recovery import validate_draft,default_recovery
        validate_draft(dict(default_recovery(),package=package))
        return bool(self.run('shell','sh','-c','pidof '+package+' || true').strip())

    def launch_app(self,package):
        from recovery import validate_draft,default_recovery
        import re
        validate_draft(dict(default_recovery(),package=package))
        output=self.run('shell','cmd','package','resolve-activity','--brief','-c','android.intent.category.LAUNCHER',package).decode(errors='replace')
        component=next((line.strip() for line in output.splitlines() if re.fullmatch(re.escape(package)+r'/[A-Za-z0-9_.$]+',line.strip())),None)
        if not component:raise RuntimeError('게임 실행 아이콘을 찾지 못했습니다. 패키지 이름을 확인하세요.')
        self.run('shell','am','start','-n',component)
