"""Public GitHub release checks and verified staging; no credentials required."""
from dataclasses import dataclass
import hashlib
import json
import os
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


def filesystem_path(path):
    """Use extended Windows paths for staged license/runtime files > MAX_PATH."""
    value = str(Path(path).absolute())
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = ('\\\\?\\UNC\\' + value[2:]) if value.startswith('\\\\') else ('\\\\?\\' + value)
    return Path(value)


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
    return (len(rel) == 1 and rel[0] in ('V26Macro.exe', 'UpdateHelper.exe', 'PriceOCR.exe', 'README.md', 'TEST_RESULTS.md')) or (
        rel[0] == '_internal' and len(rel) > 1) or (
        len(rel) == 2 and rel[0] == 'assets' and rel[1] in ('catcher.png', 'app.ico','coach_advice.png','league_next_match.png','league_round_label.png','league_play_hud.png'))


def is_windows_executable(file):
    if not file.is_file():
        return False
    with file.open('rb') as source:
        return source.read(2) == b'MZ'


def package_entries(archive):
    seen = set()
    files = {}
    entries = archive.infolist()
    if len(entries) > 15000 or sum(i.file_size for i in entries) > MAX_EXTRACTED:
        raise ValueError('업데이트 압축 크기 제한을 초과했습니다.')
    for info in entries:
        allowed = safe_member(info.filename)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('업데이트에 링크 파일을 포함할 수 없습니다.')
        path = PurePosixPath(info.filename)
        key = str(path).casefold()
        if key in seen:
            raise ValueError('업데이트에 중복 경로가 있습니다.')
        seen.add(key)
        if allowed and not info.is_dir():
            files[path] = info
    for name in ('V26Macro.exe', 'UpdateHelper.exe'):
        info = files.get(PurePosixPath('V26Macro', name))
        if info is None:
            raise ValueError('업데이트 실행 파일이 없습니다.')
        with archive.open(info) as source:
            if source.read(2) != b'MZ':
                raise ValueError('업데이트 실행 파일이 잘못되었습니다.')
    if not any(path.parts[:2] == ('V26Macro', '_internal') for path in files):
        raise ValueError('업데이트 실행 환경이 없습니다.')
    return files


def extract_package(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        for path, info in package_entries(z).items():
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError('업데이트 경로가 범위를 벗어났습니다.')
            output_path = filesystem_path(target)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, output_path.open('xb') as output:
                shutil.copyfileobj(source, output)
    return destination/'V26Macro'


@dataclass(frozen=True)
class StagedUpdate:
    archive: Path
    sha256: str
    size: int


def update_directory(root):
    directory = root/'.updates'
    if directory.resolve() != directory:
        raise ValueError('업데이트 작업 폴더를 확인할 수 없습니다.')
    directory.mkdir(exist_ok=True)
    return directory


def stage_update(root, release, opener=urllib.request.urlopen, progress=lambda value: None):
    root = Path(root).resolve()
    stage = update_directory(root)/uuid.uuid4().hex
    stage.mkdir()
    archive = stage/'download.zip'
    request = urllib.request.Request(release['url'], headers={'User-Agent': f'V26Macro/{VERSION}'})
    digest = hashlib.sha256()
    total = 0
    with opener(request, timeout=20) as response, archive.open('xb') as output:
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
    with zipfile.ZipFile(archive) as z:
        package_entries(z)
    return StagedUpdate(archive, digest.hexdigest(), total)


def launch_helper(root, staged):
    root, archive = Path(root).resolve(), staged.archive.resolve()
    if not getattr(sys, 'frozen', False):
        raise ValueError('자동 교체는 Windows 실행 파일 버전에서 사용할 수 있습니다.')
    if archive.name != 'download.zip' or archive.parent.parent != root/'.updates':
        raise ValueError('잘못된 업데이트 작업 폴더입니다.')
    launcher = archive.parent/'UpdateLauncher.exe'
    shutil.copy2(root/'UpdateHelper.exe', launcher)
    return subprocess.Popen(
        [str(launcher), '--root', str(root), '--archive', str(archive),
         '--archive-sha256', staged.sha256, '--archive-size', str(staged.size), '--pid', str(os.getpid())],
        cwd=str(root), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
