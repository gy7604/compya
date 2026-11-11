"""Standalone one-file helper. Transactional runtime swap, user data untouched."""
import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from updates import MAX_DOWNLOAD, StagedUpdate, extract_package, is_windows_executable, update_directory


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


RUNTIME_NAMES = ('_internal', 'UpdateHelper.exe', 'PriceOCR.exe', 'README.md', 'TEST_RESULTS.md', 'V26Macro.exe')


@contextmanager
def installation_lock(directory):
    with (directory/'install.lock').open('a+b') as lock:
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def restore_pending(root, directory):
    pending = directory/'pending.json'
    if not pending.is_file():
        return False
    plan = json.loads(pending.read_text(encoding='utf-8'))
    names, originals, backup_name = plan['names'], plan['originals'], plan['backup']
    if (not isinstance(names, list) or names != [name for name in RUNTIME_NAMES if name in names]
            or not isinstance(originals, list) or any(name not in names for name in originals)
            or not isinstance(backup_name, str) or not re.fullmatch(r'backup_[a-f0-9]{32}', backup_name)):
        raise ValueError('업데이트 복구 기록이 올바르지 않습니다.')
    backup = directory/backup_name
    if not backup.is_dir() or backup.resolve() != backup:
        raise ValueError('업데이트 백업을 확인할 수 없습니다.')
    recovered_files = backup/'interrupted-install'
    if recovered_files.resolve() != recovered_files:
        raise ValueError('업데이트 복구 폴더가 올바르지 않습니다.')
    recovered_files.mkdir(exist_ok=True)
    for name in reversed(names):
        target, previous = root/name, backup/name
        if previous.exists():
            if target.exists():
                target.rename(recovered_files/name)
            previous.rename(target)
        elif name not in originals:
            if target.exists():
                target.rename(recovered_files/name)
        elif not target.exists():
            raise FileNotFoundError(f'업데이트 복구에 필요한 파일이 없습니다: {name}')
    pending.rename(backup/'recovered.json')
    return True


def recover_interrupted_update(root):
    root = Path(root).resolve()
    directory = update_directory(root)
    with installation_lock(directory):
        return restore_pending(root, directory)


def replace_runtime(root, package):
    root, package = Path(root).resolve(), Path(package).resolve()
    directory = update_directory(root)
    if not package.is_relative_to(directory) or package == directory:
        raise ValueError('잘못된 업데이트 설치 경로입니다.')
    for name in ('V26Macro.exe', 'UpdateHelper.exe'):
        if not is_windows_executable(package/name):
            raise ValueError('새 실행 파일을 확인할 수 없습니다.')
    if not (package/'_internal').is_dir():
        raise ValueError('새 실행 환경이 없습니다.')
    if (package/'PriceOCR.exe').exists() and not is_windows_executable(package/'PriceOCR.exe'):
        raise ValueError('새 글자 인식 실행 파일을 확인할 수 없습니다.')
    names = [name for name in RUNTIME_NAMES if (package/name).exists()]
    with installation_lock(directory):
        restore_pending(root, directory)
        if not (root/'V26Macro.exe').is_file():
            raise ValueError('잘못된 업데이트 설치 경로입니다.')
        backup = directory/('backup_'+uuid.uuid4().hex)
        backup.mkdir()
        originals = [name for name in names if (root/name).exists()]
        pending = directory/'pending.json'
        draft = backup/'journal.json'
        with draft.open('x', encoding='utf-8') as stream:
            json.dump({'backup': backup.name, 'names': names, 'originals': originals}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        draft.rename(pending)
        try:
            for name in names:
                target = root/name
                if name in originals:
                    target.rename(backup/name)
                (package/name).rename(target)
            assets = root/'assets'
            assets.mkdir(exist_ok=True)
            for name in ('catcher.png', 'app.ico', 'coach_advice.png', 'league_next_match.png',
                         'league_round_label.png', 'league_play_hud.png'):
                source, target = package/'assets'/name, assets/name
                if not target.exists() and source.is_file():
                    try:
                        if os.name == 'nt':
                            # Windows rename publishes the complete file and rejects an existing target.
                            source.rename(target)
                        else:
                            os.link(source, target)
                    except FileExistsError:
                        pass  # A user file created in the meantime takes precedence.
            pending.rename(backup/'completed.json')
        except Exception:
            restore_pending(root, directory)
            raise
    return backup


def apply_staged_update(root, staged):
    root, archive = Path(root).resolve(), staged.archive.resolve()
    if (archive.name != 'download.zip' or archive.parent.parent != root/'.updates'
            or not re.fullmatch(r'[a-f0-9]{64}', staged.sha256)
            or not 0 < staged.size <= MAX_DOWNLOAD):
        raise ValueError('업데이트 검증 정보를 확인할 수 없습니다.')
    digest = hashlib.sha256()
    total = 0
    with archive.open('rb') as source, tempfile.TemporaryFile(dir=archive.parent) as verified:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > staged.size:
                break
            digest.update(chunk)
            verified.write(chunk)
        if total != staged.size or digest.hexdigest() != staged.sha256:
            raise ValueError('설치 전 업데이트 검증에 실패했습니다. 기존 프로그램을 유지합니다.')
        verified.seek(0)
        destination = Path(tempfile.mkdtemp(prefix='install-', dir=archive.parent))
        package = extract_package(verified, destination)
    return replace_runtime(root, package)


def start_program(root):
    subprocess.Popen([str(root/'V26Macro.exe')], cwd=str(root),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    parser.add_argument('--archive')
    parser.add_argument('--archive-sha256')
    parser.add_argument('--archive-size', type=int)
    parser.add_argument('--pid', type=int)
    args = parser.parse_args()
    if len(sys.argv) == 1 and getattr(sys, 'frozen', False):
        # The standalone launcher in the stage works even when the app runtime is incomplete.
        launcher_directory = Path(sys.executable).resolve().parent
        root = (launcher_directory.parent.parent if launcher_directory.parent.name == '.updates'
                else launcher_directory)
        try:
            if recover_interrupted_update(root):
                start_program(root)
            else:
                ctypes.windll.user32.MessageBoxW(None, '복구할 업데이트가 없습니다.', 'V26 업데이트', 0x40)
        except Exception as e:
            ctypes.windll.user32.MessageBoxW(None, str(e), 'V26 업데이트 복구', 0x10)
            raise SystemExit(1)
        return
    if not all((args.root, args.archive, args.archive_sha256, args.archive_size, args.pid)):
        parser.error('업데이트 파일과 검증 정보가 필요합니다.')
    root = Path(args.root).resolve()
    try:
        wait_for_parent(args.pid)
        staged = StagedUpdate(Path(args.archive), args.archive_sha256, args.archive_size)
        backup = apply_staged_update(root, staged)
        (root/'update_result.json').write_text(json.dumps({'status': 'ok', 'backup': str(backup)}), encoding='utf-8')
        start_program(root)
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(
            None, f'업데이트를 완료하지 못했습니다.\n{e}\n교체 중 프로그램이 종료되었다면 작업 폴더의 UpdateLauncher.exe를 실행하여 복구하세요.',
            'V26 업데이트', 0x10,
        )
        raise SystemExit(1)


if __name__ == '__main__':
    main()
