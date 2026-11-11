import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch
import urllib.error
import zipfile
from updates import check_latest, stage_update, extract_package, version_tuple, UPDATE_ASSET, filesystem_path, VERSION
import updater
from updater import replace_runtime
from package_release import build_archive


class Response(io.BytesIO):
    def geturl(self):
        return 'https://release-assets.githubusercontent.com/test'


def release(tag='v9.0.0'):
    return dict(tag_name=tag, assets=[dict(name=UPDATE_ASSET, size=100,
        digest='sha256:'+'a'*64,
        browser_download_url=f'https://github.com/gy7604/compya/releases/download/{tag}/{UPDATE_ASSET}')])


def archive_bytes(extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        for name, body in {
            'V26Macro/V26Macro.exe': b'MZnew', 'V26Macro/UpdateHelper.exe': b'MZhelper',
            'V26Macro/_internal/runtime': b'new', 'V26Macro/buttons.json': b'never import',
            **(extra or {})}.items():
            z.writestr(name, body)
    return data.getvalue()


class UpdateTests(unittest.TestCase):
    def test_release_archive_excludes_user_settings_and_images(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, _ = self.prepare_install(folder)
            archive = root/'release.zip'
            build_archive(root, archive)
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                self.assertIn('V26Macro/V26Macro.exe', names)
                self.assertFalse(any('buttons.json' in n or 'rules.json' in n or 'progress.json' in n or 'notifications.json' in n or 'ranking.json' in n or 'recovery.json' in n or 'last_settings.json' in n or 'custom.png' in n for n in names))

    def test_version_order(self):
        self.assertGreater(version_tuple('v1.10.0'), version_tuple('1.9.0'))
        with self.assertRaises(ValueError):
            version_tuple('v1.0.0-beta')

    def test_newer_release(self):
        result = check_latest(lambda *a, **k: Response(json.dumps(release()).encode()))
        self.assertEqual(result['tag'], 'v9.0.0')

    def test_current_or_older_release(self):
        self.assertIsNone(check_latest(lambda *a, **k: Response(json.dumps(release('v'+VERSION)).encode())))

    def test_deep_windows_extraction(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            zip_path = Path(folder)/'update.zip'
            name = 'V26Macro/_internal/licenses/' + '/'.join(['long_license_directory']*10) + '/LICENSE'
            zip_path.write_bytes(archive_bytes({name: b'license text'}))
            extract_package(zip_path, Path(folder)/'extracted')
            deep = filesystem_path(Path(folder)/'extracted'/name)
            self.assertEqual(deep.read_bytes(), b'license text')
            # Remove with the same extended path before TemporaryDirectory cleanup.
            for _ in range(11):
                parent = deep.parent
                if deep.is_file():
                    deep.unlink()
                elif deep.is_dir():
                    deep.rmdir()
                deep = parent

    def test_no_published_release(self):
        def opener(*a, **kw):
            raise urllib.error.HTTPError('url', 404, '', {}, None)
        self.assertIsNone(check_latest(opener))

    def test_bad_download_url_or_missing_digest(self):
        for key, value in [('browser_download_url', 'https://example.com/file.zip'), ('digest', None)]:
            data = release()
            data['assets'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_latest(lambda *a, **k: Response(json.dumps(data).encode()))

    def test_corrupt_download_never_extracts(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            r = dict(url='https://github.com/a', size=3, sha256='0'*64)
            with self.assertRaises(ValueError):
                stage_update(folder, r, lambda *a, **k: Response(b'bad'))
            self.assertFalse((Path(folder)/'V26Macro.exe').exists())

    def test_verified_download_and_user_data_not_extracted(self):
        data = archive_bytes()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            r = dict(url='https://github.com/a', size=len(data), sha256=hashlib.sha256(data).hexdigest())
            root, _ = self.prepare_install(folder)
            staged = stage_update(root, r, lambda *a, **k: Response(data))
            self.assertTrue(staged.archive.is_file())
            self.assertFalse((staged.archive.parent/'unpacked').exists())
            updater.apply_staged_update(root, staged)
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZnew')
            self.assert_user_data(root)

    def test_changed_staged_download_keeps_the_previous_runtime(self):
        data = archive_bytes()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, _ = self.prepare_install(folder)
            release = dict(url='https://github.com/a', size=len(data), sha256=hashlib.sha256(data).hexdigest())
            staged = stage_update(root, release, lambda *a, **k: Response(data))
            # Simulate a staged file becoming corrupted before the helper starts.
            staged.archive.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            with self.assertRaisesRegex(ValueError, '설치 전 업데이트 검증'):
                updater.apply_staged_update(root, staged)
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZold')
            self.assertEqual((root/'_internal'/'runtime').read_bytes(), b'old')
            self.assert_user_data(root)

    def test_zip_path_traversal_rejected(self):
        for name in ['V26Macro/../../escape', '/escape', 'V26Macro/..\\escape', 'V26Macro/C:/escape']:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
                zip_path = Path(folder)/'update.zip'
                zip_path.write_bytes(archive_bytes({name: b'no'}))
                with self.assertRaises(ValueError):
                    extract_package(zip_path, Path(folder)/'extracted')

    def prepare_install(self, folder):
        root = Path(folder)
        package = root/'.updates'/'stage'/'V26Macro'
        package.mkdir(parents=True)
        for parent, content in [(root, b'old'), (package, b'new')]:
            (parent/'V26Macro.exe').write_bytes(b'MZ'+content)
            (parent/'UpdateHelper.exe').write_bytes(b'MZ'+content)
            (parent/'_internal').mkdir()
            (parent/'_internal'/'runtime').write_bytes(content)
        for name in ('buttons.json', 'last_settings.json', 'rules.json', 'progress.json', 'notifications.json', 'ranking.json', 'recovery.json'):
            (root/name).write_bytes(b'user settings')
        (root/'assets'/'buttons').mkdir(parents=True)
        (root/'assets'/'buttons'/'custom.png').write_bytes(b'user image')
        return root, package

    def assert_user_data(self, root):
        self.assertEqual((root/'buttons.json').read_bytes(), b'user settings')
        self.assertEqual((root/'last_settings.json').read_bytes(), b'user settings')
        self.assertEqual((root/'rules.json').read_bytes(), b'user settings')
        self.assertEqual((root/'progress.json').read_bytes(), b'user settings')
        self.assertEqual((root/'notifications.json').read_bytes(), b'user settings')
        self.assertEqual((root/'ranking.json').read_bytes(), b'user settings')
        self.assertEqual((root/'recovery.json').read_bytes(), b'user settings')
        self.assertEqual((root/'assets'/'buttons'/'custom.png').read_bytes(), b'user image')

    def test_replace_preserves_settings_images_and_backup(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, package = self.prepare_install(folder)
            backup = replace_runtime(root, package)
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZnew')
            self.assertEqual((backup/'V26Macro.exe').read_bytes(), b'MZold')
            self.assert_user_data(root)

    def test_season_assets_and_ocr_runtime_delivered_without_overwriting_user_data(self):
        from updates import safe_member
        for name in ('PriceOCR.exe','assets/league_next_match.png','assets/league_round_label.png'):
            self.assertTrue(safe_member('V26Macro/'+name))
        self.assertFalse(safe_member('V26Macro/league_season.json'))
        self.assertFalse(safe_member('V26Macro/league_timing.json'))
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root,package=self.prepare_install(folder)
            (root/'PriceOCR.exe').write_bytes(b'MZoldocr')
            (root/'league_season.json').write_bytes(b'private progress')
            (root/'league_timing.json').write_bytes(b'private durations')
            (package/'PriceOCR.exe').write_bytes(b'MZnewocr')
            (package/'assets').mkdir()
            for name in ('league_next_match.png','league_round_label.png'):(package/'assets'/name).write_bytes(b'new image')
            (root/'assets/league_next_match.png').write_bytes(b'preserved image')
            backup=replace_runtime(root,package)
            self.assertEqual((root/'PriceOCR.exe').read_bytes(),b'MZnewocr')
            self.assertEqual((backup/'PriceOCR.exe').read_bytes(),b'MZoldocr')
            self.assertEqual((root/'assets/league_next_match.png').read_bytes(),b'preserved image')
            self.assertEqual((root/'assets/league_round_label.png').read_bytes(),b'new image')
            self.assertEqual((root/'league_season.json').read_bytes(),b'private progress')
            self.assertEqual((root/'league_timing.json').read_bytes(),b'private durations')
            self.assert_user_data(root)

    def test_update_does_not_replace_an_asset_created_during_publication(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, package = self.prepare_install(folder)
            staged_asset = package/'assets'/'league_round_label.png'
            staged_asset.parent.mkdir()
            staged_asset.write_bytes(b'complete factory image')
            installed_asset = root/'assets'/staged_asset.name
            publish = Path.rename if os.name == 'nt' else os.link

            def create_user_asset(source, target):
                if source == staged_asset:
                    installed_asset.write_bytes(b'user image created during update')
                return publish(source, target)

            owner, method = (Path, 'rename') if os.name == 'nt' else (os, 'link')
            with patch.object(owner, method, create_user_asset):
                replace_runtime(root, package)
            self.assertEqual(installed_asset.read_bytes(), b'user image created during update')
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZnew')
            self.assert_user_data(root)

    def test_invalid_optional_ocr_runtime_does_not_start_replacement(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root,package=self.prepare_install(folder)
            (package/'PriceOCR.exe').write_bytes(b'bad')
            with self.assertRaises(ValueError):replace_runtime(root,package)
            self.assertEqual((root/'V26Macro.exe').read_bytes(),b'MZold')

    def test_launch_passes_verified_archive_metadata_to_standalone_helper(self):
        from updates import launch_helper
        data = archive_bytes()
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, _ = self.prepare_install(folder)
            release = dict(url='https://github.com/a', size=len(data), sha256=digest)
            staged = stage_update(root, release, lambda *a, **k: Response(data))
            with patch.object(sys, 'frozen', True, create=True), patch('updates.subprocess.Popen') as popen:
                launch_helper(root, staged)
            arguments = popen.call_args.args[0]
            self.assertIn('--archive', arguments)
            self.assertIn(digest, arguments)
            self.assertIn(str(len(data)), arguments)

    def test_runtime_can_be_restored_after_an_abrupt_exit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, package = self.prepare_install(folder)
            original = Path.rename

            def rename(path, target):
                result = original(path, target)
                if path == package/'_internal':
                    raise KeyboardInterrupt('simulated process exit')
                return result

            with patch.object(Path, 'rename', rename), self.assertRaises(KeyboardInterrupt):
                replace_runtime(root, package)
            self.assertTrue((root/'.updates'/'pending.json').is_file())
            self.assertTrue(updater.recover_interrupted_update(root))
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZold')
            self.assertEqual((root/'_internal'/'runtime').read_bytes(), b'old')
            self.assertFalse(updater.recover_interrupted_update(root))
            self.assert_user_data(root)

    def test_standalone_recovery_restores_an_install_without_the_main_executable(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, package = self.prepare_install(folder)
            root = root.resolve()
            launcher = root/'.updates'/'stage'/'UpdateLauncher.exe'
            launcher.write_bytes(b'MZrecovery helper')
            original = Path.rename

            def rename(path, target):
                result = original(path, target)
                if path == root/'V26Macro.exe':
                    raise KeyboardInterrupt('simulated exit while replacing the executable')
                return result

            with patch.object(Path, 'rename', rename), self.assertRaises(KeyboardInterrupt):
                replace_runtime(root, package)
            self.assertFalse((root/'V26Macro.exe').exists())
            with (patch.object(sys, 'frozen', True, create=True),
                  patch.object(sys, 'argv', [str(launcher)]),
                  patch.object(sys, 'executable', str(launcher)),
                  patch('updater.subprocess.Popen') as popen):
                updater.main()
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZold')
            self.assertEqual((root/'_internal'/'runtime').read_bytes(), b'old')
            self.assertEqual(popen.call_args.args[0], [str(root/'V26Macro.exe')])
            self.assert_user_data(root)

    def test_failed_replacement_rolls_back(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root, package = self.prepare_install(folder)
            original = Path.rename
            def rename(path, target):
                if path == package/'_internal':
                    raise OSError('simulated locked file')
                return original(path, target)
            with patch.object(Path, 'rename', rename), self.assertRaises(OSError):
                replace_runtime(root, package)
            self.assertEqual((root/'V26Macro.exe').read_bytes(), b'MZold')
            self.assertEqual((root/'_internal'/'runtime').read_bytes(), b'old')
            self.assert_user_data(root)
