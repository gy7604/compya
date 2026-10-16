import threading
import unittest
from unittest.mock import Mock, patch
from app import App
from registry import new_button


class RunnerTests(unittest.TestCase):
    def run_sequence(self, dry):
        app = App.__new__(App)
        app.stop = threading.Event()
        app.emit = Mock()
        app.registry = Mock()
        app.registry.resolve.side_effect = lambda x: x
        adb = Mock()
        # PLAY BALL stable, disappears, then pitching button stabilizes.
        adb.screenshot.side_effect = list(range(9)) + [RuntimeError('offline')]
        def factory(path, *args):
            m = Mock()
            m.find.side_effect = lambda frame: (
                (frame < 3 if path == 'pb.png' else frame >= 3),
                (100, 200) if path == 'pb.png' else (300, 400), .99, (720, 1280))
            return m
        buttons = [new_button('PLAY BALL'), new_button('포수 리드'), new_button('꺼진 버튼')]
        for r, path in zip(buttons, ['pb.png', 'pitch.png']):
            r.update(enabled=True, template=path)
        cfg = dict(buttons=buttons, adb='adb', serial='emulator-5554', wait=180, dry=dry)
        with patch('app.ADB', return_value=adb), patch('vision.Matcher', side_effect=factory), \
             patch('app.time.monotonic', side_effect=range(100)):
            app.run(cfg)
        self.assertTrue(app.stop.is_set())
        return adb, app

    def test_actual_tap_order_and_connection_stop(self):
        adb, app = self.run_sequence(False)
        self.assertEqual([c.args[0] for c in adb.tap.call_args_list], [(100, 200), (300, 400)])
        self.assertTrue(any('offline' in c.args[0] for c in app.emit.call_args_list))

    def test_dry_run_never_sends_taps(self):
        adb, _ = self.run_sequence(True)
        adb.tap.assert_not_called()
