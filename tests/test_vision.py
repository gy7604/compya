import unittest
import threading
from unittest.mock import patch
from pathlib import Path
import cv2
import numpy as np
from vision import Matcher,decode_screen

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

    def test_repeated_detectors_share_decoding_without_stale_next_frame(self):
        png=self.encode(self.template)
        with patch('vision.cv2.imdecode',wraps=cv2.imdecode) as decode:
            first=self.matcher.find(png)
            self.assertEqual(self.matcher.find(png),first)
            self.assertEqual(decode.call_count,1)
            blank=self.encode(np.full(self.template.shape,120,np.uint8))
            self.assertFalse(self.matcher.find(blank)[0])
            self.assertEqual(decode.call_count,2)

    def test_native_gray_and_color_results_preserved_and_thread_isolated(self):
        rng=np.random.default_rng(42)
        image=rng.integers(0,256,(80,120,3),dtype=np.uint8)
        png=self.encode(image)
        expected_gray=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_GRAYSCALE)
        expected_color=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
        with patch('vision.cv2.imdecode',wraps=cv2.imdecode) as decode:
            np.testing.assert_array_equal(decode_screen(png),expected_gray)
            np.testing.assert_array_equal(decode_screen(png,True),expected_color)
            decode_screen(png);decode_screen(png,True)
            self.assertEqual(decode.call_count,2)
            worker=threading.Thread(target=lambda:decode_screen(png))
            worker.start();worker.join()
            self.assertEqual(decode.call_count,3)

    def test_mutable_inputs_cannot_reuse_an_old_decoded_image(self):
        png=bytearray(self.encode(np.zeros((20,20),np.uint8)))
        self.assertEqual(int(decode_screen(png)[0,0]),0)
        png[:]=self.encode(np.full((20,20),200,np.uint8))
        self.assertEqual(int(decode_screen(png)[0,0]),200)
