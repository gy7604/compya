"""Public GitHub release checks and verified staging; no credentials required."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from version import VERSION, REPOSITORY, UPDATE_ASSET

MAX_DOWNLOAD = 600 * 1024 * 1024
MAX_EXTRACTED = 1500 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('지원하지 않는 버전 형식입니다.')
    return tuple(map(int, match.groups()))


def check_latest(opener=urllib.request.urlopen):
    request = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/releases/latest',
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': f'V26Macro/{VERSION}'})
    try:
        with opener(request, timeout=10) as response:
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 404:
            return None
        raise RuntimeError(f'업데이트 서버 응답 오류 ({e.code})') from e
    if len(raw) > 2_000_000:
        raise ValueError('업데이트 정보가 너무 큽니다.')
    release = json.loads(raw)
    if release.get('draft') or release.get('prerelease'):
        return None
    tag = release['tag_name']
    if version_tuple(tag) <= version_tuple(VERSION):
        return None
    asset = next((a for a in release.get('assets', []) if a['name'] == UPDATE_ASSET), None)
    if asset is None:
        raise ValueError('새 버전의 Windows 파일이 아직 준비되지 않았습니다.')
    url = asset['browser_download_url']
    expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{UPDATE_ASSET}'
    if url != expected:
        raise ValueError('업데이트 다운로드 주소가 올바르지 않습니다.')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
        raise ValueError('업데이트 파일의 검증 정보가 없습니다.')
    size = asset['size']
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError('업데이트 파일 크기가 올바르지 않습니다.')
    return dict(tag=tag, url=url, sha256=digest[7:].lower(), size=size)


def safe_member(name):
    if '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
        raise ValueError('잘못된 업데이트 경로입니다.')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != 'V26Macro':
        raise ValueError('업데이트 파일의 폴더 구조가 올바르지 않습니다.')
    if any(part.endswith((' ', '.')) for part in p.parts):
        raise ValueError('잘못된 Windows 경로입니다.')
    rel = p.parts[1:]
    if not rel:
        return False
    # Configuration and user images never come from an update package.
    return (len(rel) == 1 and rel[0] in ('V26Macro.exe', 'UpdateHelper.exe', 'README.md', 'TEST_RESULTS.md')) or (
        rel[0] == '_internal' and len(rel) > 1) or (
        len(rel) == 2 and rel[0] == 'assets' and rel[1] in ('catcher.png', 'app.ico'))


def extract_package(archive, destination):
    destination = Path(destination)
    seen = set()
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > 15000 or sum(i.file_size for i in z.infolist()) > MAX_EXTRACTED:
            raise ValueError('업데이트 압축 크기 제한을 초과했습니다.')
        for info in z.infolist():
            allowed = safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('업데이트에 링크 파일을 포함할 수 없습니다.')
            key = info.filename.casefold().rstrip('/')
            if key in seen:
                raise ValueError('업데이트에 중복 경로가 있습니다.')
            seen.add(key)
            if info.is_dir() or not allowed:
                continue
            target = destination.joinpath(*PurePosixPath(info.filename).parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('업데이트 경로가 범위를 벗어났습니다.')
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
    package = destination/'V26Macro'
    for name in ('V26Macro.exe', 'UpdateHelper.exe'):
        if not (package/name).is_file() or (package/name).read_bytes()[:2] != b'MZ':
            raise ValueError('업데이트 실행 파일이 없거나 잘못되었습니다.')
    if not (package/'_internal').is_dir():
        raise ValueError('업데이트 실행 환경이 없습니다.')
    return package


def stage_update(root, release, opener=urllib.request.urlopen, progress=lambda value: None):
    root = Path(root).resolve()
    stage = root/'.updates'/uuid.uuid4().hex
    stage.mkdir(parents=True)
    archive = stage/'download.zip'
    request = urllib.request.Request(release['url'], headers={'User-Agent': f'V26Macro/{VERSION}'})
    digest = hashlib.sha256()
    total = 0
    try:
        with opener(request, timeout=20) as response, archive.open('wb') as output:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != 'https' or final.hostname not in (
                'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
                raise ValueError('허용되지 않은 다운로드 서버입니다.')
            deadline = time.monotonic() + 600
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > release['size'] or total > MAX_DOWNLOAD or time.monotonic() > deadline:
                    raise ValueError('다운로드 크기 또는 시간 제한을 초과했습니다.')
                output.write(chunk)
                digest.update(chunk)
                progress(int(total * 100 / release['size']))
        if total != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('다운로드 파일 검증에 실패했습니다. 기존 프로그램을 유지합니다.')
        return extract_package(archive, stage/'unpacked')
    except Exception:
        # Retain any staged diagnostics; live application is untouched.
        raise


def launch_helper(root, package):
    root, package = Path(root).resolve(), Path(package).resolve()
    if not getattr(sys, 'frozen', False):
        raise ValueError('자동 교체는 Windows 실행 파일 버전에서 사용할 수 있습니다.')
    if not package.is_relative_to(root/'.updates'):
        raise ValueError('잘못된 업데이트 작업 폴더입니다.')
    launcher = package.parent.parent/'UpdateLauncher.exe'
    shutil.copy2(root/'UpdateHelper.exe', launcher)
    return subprocess.Popen([str(launcher), '--root', str(root), '--package', str(package), '--pid', str(__import__('os').getpid())],
        cwd=str(root), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
