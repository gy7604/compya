import copy
import tempfile
from pathlib import Path
import unittest
from capture import scaled_point
from clicks import click_point
from registry import new_button, validate, Registry


class ClickTests(unittest.TestCase):
    def record(self):
        r=new_button('PLAY BALL')
        r.update(click_mode='fixed',click_point=[600,900],click_size=[720,1280])
        return r

    def test_legacy_record_uses_matched_center(self):
        self.assertEqual(click_point({'name':'old'},(100,200),(720,1280)),(100,200))

    def test_fixed_coordinate_does_not_follow_matched_image(self):
        self.assertEqual(click_point(self.record(),(100,200),(720,1280)),(600,900))

    def test_screen_resolution_change_rejected(self):
        with self.assertRaises(ValueError):click_point(self.record(),(100,200),(1080,1920))

    def test_invalid_coordinate_rejected_before_tap(self):
        for point in [[720,10],[-1,10],[1.5,10],[True,10]]:
            with self.subTest(point=point),self.assertRaises(ValueError):
                r=self.record();r['click_point']=point
                click_point(r,(100,200),(720,1280))

    def test_scaled_selection_uses_adb_pixels(self):
        self.assertEqual(scaled_point((300,450),2,720,1280),(600,900))
        with self.assertRaises(ValueError):scaled_point((360,450),2,720,1280)

    def test_fixed_coordinate_persists(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=Registry(folder);record=self.record()
            store.save([record])
            self.assertEqual(store.load({})[0]['click_point'],[600,900])

    def test_fixed_requires_reference_resolution_and_inside_bounds(self):
        for changes in [dict(click_size=None),dict(click_point=[720,0]),dict(click_mode='bad')]:
            r=self.record();r.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate([r])
