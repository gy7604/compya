import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from coach_dialog import CoachDialog
from app import App
from test_recovery import fixture

ROOT=Path(__file__).resolve().parents[1]


def scene(dialog,title=True,cancel=True):
    image=np.zeros((1280,720,3),np.uint8)
    for shown,matcher,point in [(title,dialog.title,(280,130)),(cancel,dialog.cancel,(376,268))]:
        if shown:
            x,y=point;h,w=matcher.template.shape;image[y:y+h,x:x+w]=cv2.cvtColor(matcher.template,cv2.COLOR_GRAY2BGR)
    image[175:245,280:700]=(40,65,90)  # Arbitrary changing recommendation text.
    return cv2.imencode('.png',image)[1].tobytes()


class CoachDialogTests(unittest.TestCase):
    def test_variable_advice_body_does_not_hide_title_and_cancel(self):
        dialog=CoachDialog(ROOT);result=dialog.find(scene(dialog));self.assertTrue(result[0]);self.assertEqual(result[1],(454,293))

    def test_title_and_cancel_must_both_exist_at_the_registered_resolution(self):
        dialog=CoachDialog(ROOT)
        for title,cancel in [(True,False),(False,True),(False,False)]:self.assertFalse(dialog.find(scene(dialog,title,cancel))[0])
        small=cv2.imencode('.png',np.zeros((720,1280,3),np.uint8))[1].tobytes();self.assertFalse(dialog.find(small)[0])

    def test_coach_cancels_before_video_rest_sponsor_and_recovery_even_while_route_active(self):
        records,recovery=fixture();recovery['target']='league'
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False);app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda value:value
        adb=Mock();adb.app_running.return_value=True;adb.screenshot.return_value=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes();adb.tap.side_effect=lambda point:app.stop.set()
        marker=Mock();marker.find.return_value=(True,(454,293),.999,(720,1280))
        def matcher(*args,**kwargs):
            value=Mock();value.find.return_value=(False,(100,200),.2,(720,1280));return value
        with tempfile.TemporaryDirectory() as folder:
            assets=Path(folder)/'assets';assets.mkdir();(assets/'coach_title_v2.png').touch();(assets/'coach_cancel_v2.png').touch()
            with patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),patch('coach_dialog.CoachDialog',return_value=marker),patch('app.BallRest') as rest,patch('app.Sponsor') as sponsor:
                rest.return_value.events=[];rest.return_value.active=False
                sponsor.return_value.events=[];sponsor.return_value.active=False
                app.run_session(dict(buttons=records,recovery=recovery,coach_advice=True,dry=False,adb='adb',serial='device',_activate_route=True))
        adb.tap.assert_called_once_with((454,292));adb.stop_app.assert_not_called();adb.launch_app.assert_not_called();sponsor.return_value.observe.assert_not_called();rest.return_value.submit.assert_not_called()
