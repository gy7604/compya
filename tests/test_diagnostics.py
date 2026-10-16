import json
from pathlib import Path
import tempfile
import unittest
from actions import ActionMachine
from diagnostics import write_stop_snapshot
from engine import SafetyStop


class DiagnosticsTests(unittest.TestCase):
    def test_timeout_identifies_button_by_name_not_id(self):
        identity = '718085c152ed4cf7af18130f4d16a6a0'
        m = ActionMachine(0, policies={identity: {'name': 'PLAY BALL'}})
        for t in [0, .25, .5]:
            m.observe({identity: True}, t)
        with self.assertRaises(SafetyStop) as error:
            m.observe({identity: True}, 8.5)
        self.assertIn('PLAY BALL', str(error.exception))
        self.assertIn('8초', str(error.exception))
        self.assertNotIn(identity, str(error.exception))

    def test_unrecognized_screen_is_a_separate_message(self):
        m = ActionMachine(0, timeout=10)
        with self.assertRaises(SafetyStop) as error:
            m.observe({}, 10)
        self.assertIn('버튼을 찾지 못했습니다', str(error.exception))

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
