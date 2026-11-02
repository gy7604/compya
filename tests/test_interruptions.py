import unittest
from pathlib import Path
import cv2
import numpy as np
from interruptions import CoachAdvice
from engine import SafetyStop
from vision import Matcher


class CoachTests(unittest.TestCase):
    def test_cancel_only_stable_popup_and_resume_after_three_absent_frames(self):
        guard=CoachAdvice()
        self.assertEqual([guard.observe(True,i) for i in range(3)],[False,False,True])
        self.assertTrue(guard.hold)
        self.assertFalse(guard.observe(True,4))
        for i in range(2):guard.observe(False,5+i);self.assertTrue(guard.hold)
        guard.observe(False,7);self.assertTrue(guard.resumed);self.assertFalse(guard.hold)

    def test_retries_bounded_and_noise_never_clicked(self):
        guard=CoachAdvice()
        for i in range(10):self.assertFalse(guard.observe(i%2==0,i))
        guard=CoachAdvice()
        for t in (0,1,2,10,18):guard.observe(True,t)
        with self.assertRaises(SafetyStop):guard.observe(True,26)

    def test_coach_template_matches_only_its_registered_location(self):
        path=Path(__file__).resolve().parents[1]/'assets'/'coach_advice.png'
        patch=cv2.imdecode(np.fromfile(path,np.uint8),1)
        screen=np.zeros((1280,720,3),np.uint8)
        screen[130:320,280:705]=patch
        matcher=Matcher(path,.94,[265,120,450,215])
        self.assertTrue(matcher.find(cv2.imencode('.png',screen)[1].tobytes())[0])
        screen[:]=0
        self.assertFalse(matcher.find(cv2.imencode('.png',screen)[1].tobytes())[0])
