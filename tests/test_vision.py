import unittest
from pathlib import Path
import cv2
import numpy as np
from vision import Matcher

TEMPLATE = Path(__file__).resolve().parents[1]/'assets'/'catcher.png'


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.matcher = Matcher(TEMPLATE)
        self.template = cv2.imdecode(np.fromfile(TEMPLATE, np.uint8), cv2.IMREAD_GRAYSCALE)

    def encode(self, screen):
        return cv2.imencode('.png', screen)[1].tobytes()

    def test_real_template_center_and_roi(self):
        screen = np.full((400, 600), 120, np.uint8)
        h, w = self.template.shape
        screen[200:200+h, 400:400+w] = self.template
        m = Matcher(TEMPLATE, roi=(350, 150, 200, 200))
        found, point, score, size = m.find(self.encode(screen))
        self.assertTrue(found)
        self.assertEqual(point, (400+w//2, 200+h//2))
        self.assertEqual(size, (600, 400))

    def test_no_button(self):
        found, *_ = self.matcher.find(self.encode(np.full((400, 600), 120, np.uint8)))
        self.assertFalse(found)

    def test_bad_png(self):
        with self.assertRaises(ValueError):
            self.matcher.find(b'not a png')

    def test_bad_roi(self):
        m = Matcher(TEMPLATE, roi=(590, 0, 200, 200))
        with self.assertRaises(ValueError):
            m.find(self.encode(np.zeros((400, 600), np.uint8)))
