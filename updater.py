"""Standalone one-file helper. Transactional runtime swap, user data untouched."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


def wait_for_parent(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # already exited
            return
        raise OSError('프로그램 종료 상태를 확인할 수 없습니다.')
    try:
        if kernel.WaitForSingleObject(handle, 60000) != 0:
            raise TimeoutError('기존 프로그램이 종료되지 않아 업데이트하지 않았습니다.')
    finally:
        kernel.CloseHandle(handle)


def replace_runtime(root, package):
    root, package = Path(root).resolve(), Path(package).resolve()
    if not package.is_relative_to(root/'.updates') or not (root/'V26Macro.exe').is_file():
        raise ValueError('잘못된 업데이트 설치 경로입니다.')
    for name in ('V26Macro.exe', 'UpdateHelper.exe'):
        if not (package/name).is_file() or (package/name).read_bytes()[:2] != b'MZ':
            raise ValueError('새 실행 파일을 확인할 수 없습니다.')
    if not (package/'_internal').is_dir():
        raise ValueError('새 실행 환경이 없습니다.')
    backup = root/'.updates'/('backup_'+uuid.uuid4().hex)
    backup.mkdir()
    names = ['V26Macro.exe', '_internal', 'UpdateHelper.exe']
    names += [name for name in ('README.md', 'TEST_RESULTS.md') if (package/name).exists()]
    moved, installed = [], []
    try:
        for name in names:
            target = root/name
            if target.exists():
                target.rename(backup/name)
                moved.append(name)
            (package/name).rename(target)
            installed.append(name)
        assets = root/'assets'
        assets.mkdir(exist_ok=True)
        for name in ('catcher.png', 'app.ico'):
            if not (assets/name).exists() and (package/'assets'/name).exists():
                shutil.copy2(package/'assets'/name, assets/name)
    except Exception:
        for name in reversed(installed):
            p = root/name
            if p.is_dir():
                shutil.rmtree(p)  # static runtime path under validated installation root
            else:
                p.unlink(missing_ok=True)
        for name in reversed(moved):
            (backup/name).rename(root/name)
        raise
    # Backup retained for manual recovery. No user files are modified.
    return backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--pid', type=int, required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        wait_for_parent(args.pid)
        backup = replace_runtime(root, args.package)
        (root/'update_result.json').write_text(json.dumps({'status': 'ok', 'backup': str(backup)}), encoding='utf-8')
        subprocess.Popen([str(root/'V26Macro.exe')], cwd=str(root),
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(None, f'업데이트를 완료하지 못했습니다.\n{e}\n기존 파일 또는 .updates의 백업을 확인하세요.', 'V26 업데이트', 0x10)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
