import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime_resources import FILES,ensure_runtime_resources
from updates import extract_package,safe_member
from updater import replace_runtime
from package_release import build_archive


PNG=b'\x89PNG\r\n\x1a\nimage'


class ResourceTests(unittest.TestCase):
    def bundle(self,root):
        path=Path(root)/'_internal/compya_resources';path.mkdir(parents=True)
        for name in FILES:(path/name).write_bytes(b'MZocr' if name.endswith('.exe') else PNG)

    def test_only_missing_fixed_resources_are_created(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);self.bundle(root)
            (root/'_internal/compya_resources/buttons.json').write_bytes(b'never import')
            (root/'assets').mkdir();(root/'assets/coach_advice.png').write_bytes(b'custom image')
            (root/'ranking.json').write_bytes(b'private settings')
            self.assertEqual(ensure_runtime_resources(root),[])
            self.assertEqual((root/'PriceOCR.exe').read_bytes(),b'MZocr')
            self.assertEqual((root/'assets/league_next_match.png').read_bytes(),PNG)
            self.assertEqual((root/'assets/coach_advice.png').read_bytes(),b'custom image')
            self.assertEqual((root/'ranking.json').read_bytes(),b'private settings')
            self.assertFalse((root/'buttons.json').exists())
            (root/'PriceOCR.exe').write_bytes(b'MZexisting')
            ensure_runtime_resources(root)
            self.assertEqual((root/'PriceOCR.exe').read_bytes(),b'MZexisting')

    def test_legacy_update_filters_new_root_files_but_bootstrap_restores_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);source=base/'source';self.bundle(source)
            (source/'V26Macro.exe').write_bytes(b'MZnew');(source/'UpdateHelper.exe').write_bytes(b'MZhelper')
            (source/'buttons.json').write_bytes(b'private settings')
            archive=base/'release.zip';build_archive(source,archive)
            root=base/'installed';root.mkdir();(root/'V26Macro.exe').write_bytes(b'MZold')
            (root/'buttons.json').write_bytes(b'preserved config')
            def legacy_member(name):
                return safe_member(name) and ('/_internal/' in name or name in ('V26Macro/V26Macro.exe','V26Macro/UpdateHelper.exe'))
            with patch('updates.safe_member',side_effect=legacy_member):
                package=extract_package(archive,root/'.updates/stage')
            replace_runtime(root,package)
            self.assertFalse((root/'PriceOCR.exe').exists())
            self.assertEqual(ensure_runtime_resources(root),[])
            self.assertEqual((root/'PriceOCR.exe').read_bytes(),b'MZocr')
            self.assertTrue((root/'assets/league_round_label.png').is_file())
            self.assertEqual((root/'buttons.json').read_bytes(),b'preserved config')

    def test_invalid_or_unwritable_resource_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);self.bundle(root)
            (root/'_internal/compya_resources/PriceOCR.exe').write_bytes(b'bad')
            self.assertEqual(ensure_runtime_resources(root),['PriceOCR.exe'])
            self.assertFalse((root/'PriceOCR.exe').exists())
        with tempfile.TemporaryDirectory() as root:
            self.bundle(root)
            original=Path.open
            def denied(path,*args,**kwargs):
                if args and args[0]=='xb':raise PermissionError('test')
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',denied):self.assertEqual(set(ensure_runtime_resources(root)),set(FILES))

    def test_unbundled_source_checkout_does_not_modify_anything(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(ensure_runtime_resources(root),[])
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_versioned_bonus_close_is_restored_without_overwriting_old_user_asset(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);self.bundle(root);(root/'assets').mkdir()
            old=root/'assets/league_bonus_close.png';old.write_bytes(b'preserved old hand-overlay capture')
            ensure_runtime_resources(root)
            self.assertEqual((root/'assets/league_bonus_close_v2.png').read_bytes(),PNG)
            self.assertEqual(old.read_bytes(),b'preserved old hand-overlay capture')
