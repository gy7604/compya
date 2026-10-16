import json
from pathlib import Path
import tempfile
import unittest
from actions import ActionMachine
from diagnostics import write_stop_snapshot


class DiagnosticsTests(unittest.TestCase):
    def test_long_visible_button_waits_until_it_disappears(self):
        identity = '718085c152ed4cf7af18130f4d16a6a0'
        m = ActionMachine(0, policies={identity: {'name': 'PLAY BALL'}})
        for t in [0, .25, .5]:
            m.observe({identity: True}, t)
        self.assertIsNone(m.observe({identity: True},86400))
        for t in [86401,86402,86403]:m.observe({identity:False},t)
        for t in [86404,86405]:self.assertIsNone(m.observe({identity:True},t))
        self.assertEqual(m.observe({identity:True},86406),identity)

    def test_no_button_waits_indefinitely_then_detects_a_button(self):
        m = ActionMachine(0, timeout=10)
        for t in [10,180,3600,86400]:self.assertIsNone(m.observe({},t))
        for t in [86401,86402]:self.assertIsNone(m.observe({'x':True},t))
        self.assertEqual(m.observe({'x':True},86403),'x')

    def test_snapshot_and_context_are_local_and_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            records = {'x': dict(name='PLAY BALL', threshold=.9, roi=None)}
            results = {'x': (True, (100, 200), .997, (720, 1280))}
            path = write_stop_snapshot(folder, b'frame', records, results, 'x', False)
            self.assertEqual(path.read_bytes(), b'frame')
            details = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(details['buttons'][0]['name'], 'PLAY BALL')
            self.assertEqual(details['mode'], '실제 실행')
            write_stop_snapshot(folder, b'new frame', records, results, 'x', True)
            self.assertEqual(path.read_bytes(), b'new frame')
