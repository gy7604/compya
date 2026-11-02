import unittest
from pathlib import Path
import cv2
import numpy as np
from interruptions import CoachAdvice, VideoSkipper
from engine import SafetyStop
from vision import Matcher


class VideoTests(unittest.TestCase):
    def test_intro_chain_starts_only_on_registered_vs_and_stops_before_playball(self):
        guard=VideoSkipper(['vs','replay'],['vs'],['play'])
        self.assertIsNone(guard.observe({},0))
        self.assertIsNone(guard.observe({'vs':True},1))
        self.assertEqual(guard.observe({'vs':True},2),'vs')
        self.assertEqual(guard.observe({},4),'vs')
        self.assertIsNone(guard.observe({'play':True},5));self.assertTrue(guard.resumed);self.assertFalse(guard.hold)
        self.assertIsNone(guard.observe({},6))

    def test_intro_chain_has_time_and_tap_limits(self):
        for stop_time in (32,48):
            guard=VideoSkipper(['vs'],['vs'],['play'])
            guard.observe({'vs':True},0);guard.observe({'vs':True},1)
            if stop_time==32:
                for t in range(3,30,2):self.assertEqual(guard.observe({},t),'vs')
            self.assertIsNone(guard.observe({},stop_time));self.assertFalse(guard.hold)
        with self.assertRaises(ValueError):VideoSkipper(['vs'],['vs'],[])

    def test_replay_text_ignores_background_and_rejects_flat_or_other_text(self):
        import tempfile
        from vision import OverlayMatcher
        with tempfile.TemporaryDirectory() as folder:
            template=np.full((42,130,3),(30,100,50),np.uint8)
            cv2.putText(template,'REPLAY',(2,30),cv2.FONT_HERSHEY_SIMPLEX,.8,(190,190,190),2)
            path=Path(folder)/'replay.png';cv2.imwrite(str(path),template)
            matcher=OverlayMatcher(path,.9,[10,10,130,42])
            for background in ((30,100,50),(30,60,100)):
                screen=np.full((80,180,3),background,np.uint8)
                cv2.putText(screen,'REPLAY',(12,40),cv2.FONT_HERSHEY_SIMPLEX,.8,(230,230,230),2)
                self.assertTrue(matcher.find(cv2.imencode('.png',screen)[1].tobytes())[0])
            for shade in (0,255):
                screen=np.full((80,180,3),shade,np.uint8)
                self.assertFalse(matcher.find(cv2.imencode('.png',screen)[1].tobytes())[0])
            screen=np.zeros((80,180,3),np.uint8)
            cv2.putText(screen,'PLAY',(12,40),cv2.FONT_HERSHEY_SIMPLEX,.8,(230,230,230),2)
            self.assertFalse(matcher.find(cv2.imencode('.png',screen)[1].tobytes())[0])

    def test_positive_overlay_only_cooldown_bounded_and_resume(self):
        guard=VideoSkipper(['intro','replay'])
        self.assertIsNone(guard.observe({'game':True},0));self.assertFalse(guard.hold)
        self.assertIsNone(guard.observe({'intro':True},1))
        self.assertEqual(guard.observe({'intro':True},2),'intro')
        self.assertIsNone(guard.observe({'intro':True},3))
        self.assertEqual(guard.observe({'intro':True},4),'intro')
        self.assertEqual(guard.observe({'intro':True},6),'intro')
        self.assertIsNone(guard.observe({'intro':True},8));self.assertFalse(guard.hold)
        for t in (9,10,11):guard.observe({},t)
        self.assertTrue(guard.resumed)
        self.assertIsNone(guard.observe({'replay':True},12))
        self.assertEqual(guard.observe({'replay':True},13),'replay')

    def test_flicker_and_unregistered_overlay_never_tapped(self):
        guard=VideoSkipper(['intro'])
        for t in range(20):self.assertIsNone(guard.observe({'intro':t%2==0,'other':True},t))

    def test_video_registration_requires_condition_roi_and_fixed_point(self):
        from registry import new_button,validate
        r=new_button('video');r['video_skip']=True
        with self.assertRaises(ValueError):validate([r])
        r.update(kind='condition',roi=[0,0,20,20],click_mode='fixed',click_point=[10,10],click_size=[720,1280])
        validate([r])


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
