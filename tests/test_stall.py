import unittest
import cv2
import numpy as np
from stall import StallWatch
from recovery import RecoveryMachine
from test_recovery import fixture
from engine import SafetyStop
from adb import ADB
from unittest.mock import Mock

def png(value):
    return cv2.imencode('.png',np.full((160,96),value,dtype=np.uint8))[1].tobytes()

class StallTests(unittest.TestCase):
    def test_fixed_screen_triggers_at_timeout_and_reset_starts_new_window(self):
        w=StallWatch(60)
        self.assertFalse(w.observe(png(10),0))
        self.assertFalse(w.observe(png(10),58))
        self.assertTrue(w.observe(png(10),60))
        w.reset();self.assertFalse(w.observe(png(10),62))
    def test_meaningful_screen_change_resets_timer(self):
        w=StallWatch(60)
        w.observe(png(0),0)
        self.assertFalse(w.observe(png(100),58))
        self.assertFalse(w.observe(png(100),60))
        self.assertTrue(w.observe(png(100),118))
    def test_small_noise_does_not_reset_timer(self):
        w=StallWatch(60);w.observe(png(100),0)
        self.assertTrue(w.observe(png(102),60))
    def test_force_stop_only_uses_validated_package_and_shared_budget(self):
        records,cfg=fixture();cfg['max_launches']=1
        m=RecoveryMachine(cfg,records)
        self.assertTrue(m.restart_stalled(100))
        with self.assertRaises(SafetyStop):m.restart_stalled(200)
        a=ADB('adb','device');a.run=Mock()
        a.stop_app(cfg['package'])
        a.run.assert_called_once_with('shell','am','force-stop',cfg['package'])
        with self.assertRaises(ValueError):a.stop_app('bad; command')
