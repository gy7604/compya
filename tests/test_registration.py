import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from actions import ActionMachine
from capture import selection_box, save_region
from engine import SafetyStop
from vision import Matcher


class RegistrationTests(unittest.TestCase):
    def test_reverse_drag_scaled_and_clamped(self):
        self.assertEqual(selection_box((50, 60), (-5, 10), 2, 80, 100), (0, 20, 80, 100))
        with self.assertRaises(ValueError):
            selection_box((1, 1), (2, 2), 1, 100, 100)

    def test_saved_region_matches_original_coordinates(self):
        image = np.random.default_rng(12).integers(0, 255, (300, 200, 3), dtype=np.uint8)
        png = cv2.imencode('.png', image)[1].tobytes()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder)/'버튼.png'
            roi = save_region(png, (120, 200, 180, 250), path)
            found, point, score, size = Matcher(path, roi=roi).find(png)
            self.assertTrue(found)
            self.assertEqual(point, (150, 225))
            self.assertTrue(path.with_suffix('.json').exists())

    def test_playball_then_pitch(self):
        m = ActionMachine(0)
        def frame(pb, pitch, t):
            return m.observe({'PLAY BALL': pb, '포수 리드': pitch}, t)
        self.assertIsNone(frame(True, False, 0))
        self.assertIsNone(frame(True, False, .3))
        self.assertEqual(frame(True, False, .6), 'PLAY BALL')
        self.assertIsNone(frame(True, False, 1))
        for t in [1.3, 1.6, 1.9]:
            self.assertIsNone(frame(False, True, t))
        for t in [2.2, 2.5]:
            self.assertIsNone(frame(False, True, t))
        self.assertEqual(frame(False, True, 2.8), '포수 리드')

    def test_ambiguous_matches_do_not_click(self):
        m = ActionMachine(0)
        for t in range(10):
            self.assertIsNone(m.observe({'a': True, 'b': True}, t))

    def test_pending_action_blocks_other_and_times_out(self):
        m = ActionMachine(0)
        for t in [0, .25, .5]:
            m.observe({'a': True, 'b': False}, t)
        for t in [1, 2, 3]:
            self.assertIsNone(m.observe({'a': True, 'b': True}, t))
        with self.assertRaises(SafetyStop):
            m.observe({'a': True}, 8.5)
