import tempfile
import unittest
from pathlib import Path
from registry import Registry, new_button, validate
from actions import ActionMachine
from engine import SafetyStop


class RegistryTests(unittest.TestCase):
    def test_add_edit_disable_delete_and_reload(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store = Registry(folder)
            r = new_button('계속하기')
            r.update(template='assets/button.png', enabled=True)
            store.save([r])
            self.assertEqual(store.load({}), [r])
            r.update(name='다음', enabled=False, threshold=.95)
            store.save([r])
            self.assertEqual(store.load({})[0]['name'], '다음')
            self.assertFalse(store.load({})[0]['enabled'])
            store.save([])
            self.assertEqual(store.load({}), [])

    def test_duplicate_name_rejected_without_replacing_store(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store = Registry(folder)
            a, b = new_button('확인'), new_button('확인')
            store.save([a])
            with self.assertRaises(ValueError):
                store.save([a, b])
            self.assertEqual(store.load({}), [a])

    def test_legacy_migration_and_no_duplicate_migration(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store = Registry(folder)
            records = store.load(dict(template='assets/catcher.png', playball='assets/pb.png',
                                      roi='10,20,100,100', threshold=.91))
            self.assertEqual(len(records), 2)
            self.assertTrue(all(r['enabled'] for r in records))
            self.assertEqual(records[0]['roi'], [10, 20, 100, 100])
            self.assertEqual(store.load({}), records)

    def test_portable_paths(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store = Registry(folder)
            asset = Path(folder)/'assets'/'button.png'
            self.assertEqual(store.resolve(store.portable(asset)), asset)

    def test_invalid_values(self):
        for changes in [dict(threshold=float('nan')), dict(cooldown=float('inf')),
                        dict(roi=[0, 0, -1, 3]), dict(size=[0, 720]), dict(enabled=True)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                r = new_button()
                r.update(changes)
                validate([r])

    def test_per_button_safety_timeout(self):
        m = ActionMachine(0, policies={'x': {'disappear_timeout': 2, 'cooldown': 4}})
        for t in [0, .2, .4]:
            m.observe({'x': True}, t)
        with self.assertRaises(SafetyStop):
            m.observe({'x': True}, 2.4)

    def test_per_button_cooldown_blocks_next_button(self):
        m = ActionMachine(0, policies={'x': {'cooldown': 4}})
        for t in [0, .2, .4]:
            m.observe({'x': True, 'y': False}, t)
        for t in [.6, .8, 1]:
            m.observe({'x': False, 'y': True}, t)
        for t in [1.2, 1.4, 2, 3]:
            self.assertIsNone(m.observe({'x': False, 'y': True}, t))
        self.assertEqual(m.observe({'x': False, 'y': True}, 4.5), 'y')
