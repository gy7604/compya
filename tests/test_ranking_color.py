import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from vision import Matcher

class ColorTests(unittest.TestCase):
    def test_normalized_gray_matches_dimmed_button_but_color_gate_rejects_it(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            rng=np.random.default_rng(42)
            image=rng.integers(100,230,(40,100,3),dtype=np.uint8)
            path=Path(folder)/'button.png';cv2.imencode('.png',image)[1].tofile(path)
            dimmed=(image.astype(np.float32)*.45).astype(np.uint8)
            png=cv2.imencode('.png',dimmed)[1].tobytes()
            self.assertTrue(Matcher(path).find(png)[0])
            self.assertFalse(Matcher(path,color_tolerance=25).find(png)[0])
            self.assertTrue(Matcher(path,color_tolerance=25).find(cv2.imencode('.png',image)[1].tobytes())[0])
