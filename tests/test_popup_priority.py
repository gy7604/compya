import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from interruptions import PopupCloser
from engine import SafetyStop
from app import App
from test_recovery import fixture


class PopupTests(unittest.TestCase):
    def test_stable_registered_notice_closes_before_other_actions_and_waits_for_absence(self):
        guard=PopupCloser(['notice'])
        self.assertIsNone(guard.observe({'notice':True},0));self.assertTrue(guard.hold)
        self.assertIsNone(guard.observe({'notice':True},1))
        self.assertEqual(guard.observe({'notice':True},2),'notice')
        self.assertIsNone(guard.observe({},3));self.assertTrue(guard.hold)
        self.assertIsNone(guard.observe({},4));self.assertTrue(guard.hold)
        self.assertIsNone(guard.observe({},5));self.assertTrue(guard.resumed);self.assertFalse(guard.hold)

    def test_noise_and_unregistered_dialogs_never_click(self):
        guard=PopupCloser(['notice'])
        for t in range(20):self.assertIsNone(guard.observe({'notice':t%2==0,'other':True},t))

    def test_repeated_close_has_an_eight_second_gap_and_three_attempt_limit(self):
        guard=PopupCloser(['notice'])
        for t in (0,1):self.assertIsNone(guard.observe({'notice':True},t))
        self.assertEqual(guard.observe({'notice':True},2),'notice')
        self.assertIsNone(guard.observe({'notice':True},9))
        self.assertEqual(guard.observe({'notice':True},10),'notice')
        self.assertEqual(guard.observe({'notice':True},18),'notice')
        with self.assertRaises(SafetyStop):guard.observe({'notice':True},26)

    def test_next_popup_is_not_clicked_using_the_old_popups_coordinates(self):
        guard=PopupCloser(['a','b'])
        for t in (0,1,2):guard.observe({'a':True},t)
        for t in (3,4,5):self.assertIsNone(guard.observe({'b':True},t))
        self.assertIsNone(guard.observe({'b':True},9))
        self.assertEqual(guard.observe({'b':True},10),'b')

    def test_app_closes_notice_while_ball_pause_and_sponsor_both_hold(self):
        records,recovery=fixture();recovery['target']='league'
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda path:path
        png=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
        adb=Mock();adb.screenshot.return_value=png;adb.tap.side_effect=lambda point:app.stop.set()
        rest=Mock(active=True,events=[])
        sponsor=Mock(active=True,recovering=False,hold=True,events=[])
        def matcher(path,*args,**kwargs):
            obj=Mock();obj.find.return_value=(str(path)=='ad.png',(100,200),.999,(720,1280));return obj
        with tempfile.TemporaryDirectory() as folder,patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),\
                patch('vision.Matcher',side_effect=matcher),patch('app.BallRest',return_value=rest),patch('app.Sponsor',return_value=sponsor):
            app.run_session(dict(buttons=records,recovery=recovery,dry=False,adb='adb',serial='device'))
        self.assertEqual(adb.screenshot.call_count,3)
        adb.tap.assert_called_once_with((100,200));sponsor.observe.assert_not_called()
        rest.submit.assert_not_called();adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()
