import unittest
import tempfile
import threading
import itertools
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np

from engine import SafetyStop
from interruptions import PopupCloser
from popup_appearance import Appearance,read_appearances


def ad_image(kind=0,close=True):
    image=np.full((1280,720),40,np.uint8)
    rng=np.random.default_rng(kind)
    for y0,y1 in ((55,425),(470,1130)):
        cells=rng.integers(10,240,(12,12),dtype=np.uint8)
        image[y0:y1,65:655]=cv2.resize(cells,(590,y1-y0),interpolation=cv2.INTER_NEAREST)
    if close:
        cv2.circle(image,(646,1225),28,225,-1)
        cv2.line(image,(632,1211),(660,1239),30,3)
        cv2.line(image,(632,1239),(660,1211),30,3)
    return image


def png(image):return cv2.imencode('.png',image)[1].tobytes()


RECORD=dict(click_mode='fixed',size=[720,1280],roi=[93,1187,270,92],click_point=[648,1228])


class AppearanceTests(unittest.TestCase):
    def test_registered_footer_and_positive_close_shape_are_both_required(self):
        self.assertIn('ad',read_appearances(png(ad_image()),{'ad':RECORD},{'ad':True}))
        self.assertFalse(read_appearances(png(ad_image(close=False)),{'ad':RECORD},{'ad':True}))
        self.assertFalse(read_appearances(png(ad_image()),{'ad':RECORD},{'ad':False}))
        for changed in (dict(click_mode='center'),dict(size=[1280,720]),dict(roi=[20,30,200,100]),dict(click_point=[300,700])):
            self.assertFalse(read_appearances(png(ad_image()),{'ad':dict(RECORD,**changed)},{'ad':True}))
        today=dict(RECORD,click_point=[78,1226])
        self.assertIn('ad',read_appearances(png(ad_image()),{'ad':today},{'ad':True}))
        self.assertFalse(read_appearances(png(ad_image(close=False)),{'ad':today},{'ad':True}))

    def test_unknown_resolution_or_missing_x_does_not_extend_close_budget(self):
        for image in (np.zeros((720,1280),np.uint8),np.full((1280,720),225,np.uint8)):
            self.assertFalse(read_appearances(png(image),{'ad':RECORD},{'ad':True}))

    def test_two_different_bodies_and_titles_qualify_but_footer_changes_do_not(self):
        first=ad_image();last=first.copy();last[1190:1280,:]=80
        a=read_appearances(png(first),{'ad':RECORD},{'ad':True})['ad']
        b=read_appearances(png(ad_image(1)),{'ad':RECORD},{'ad':True})['ad']
        self.assertTrue(a.different_from(b));self.assertFalse(a.stable_with(b))
        # Footer/cursor changes do not enter either fingerprint region.
        last[1200:1255,610:680]=first[1200:1255,610:680]
        c=read_appearances(png(last),{'ad':RECORD},{'ad':True})['ad']
        self.assertTrue(a.stable_with(c));self.assertFalse(a.different_from(c))

    def test_body_animation_without_title_change_is_not_another_ad(self):
        a=Appearance(bytes([20])*384,bytes([20])*624)
        b=Appearance(bytes([20])*384,bytes([180])*624)
        self.assertFalse(a.different_from(b))


class PopupChainTests(unittest.TestCase):
    def test_app_dismisses_four_different_ads_with_registered_today_hide_point(self):
        from app import App
        from test_recovery import fixture
        records,cfg=fixture();cfg['target']='league'
        record=next(r for r in records if r['id']=='ad');record.update(RECORD,click_point=[78,1226])
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
        adb=Mock();adb.app_running.return_value=True;current=[0]
        frames=[png(ad_image(i)) for i in range(4)]
        adb.screenshot.side_effect=lambda:frames[current[0]]
        def tap(point):
            self.assertEqual(point,(78,1226));current[0]+=1
            if current[0]==4:app.stop.set()
        adb.tap.side_effect=tap
        def matcher(path,*args,**kwargs):
            result=Mock();result.find.return_value=(path=='ad.png',(230,1230),.99,(720,1280));return result
        with tempfile.TemporaryDirectory() as folder,patch('app.ROOT',Path(folder)),patch('patch_download.PatchDownload.scan',return_value=(False,False,None,None)),patch('app.ADB',return_value=adb), \
             patch('vision.Matcher',side_effect=matcher),patch('app.time.monotonic',side_effect=itertools.count(0,2)):
            app.run_session(dict(buttons=records,recovery=cfg,dry=False,adb='adb',serial='device'))
        self.assertEqual(adb.tap.call_count,4);adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()
        self.assertFalse(any('안전 중지' in c.args[0] for c in app.emit.call_args_list))

    def setUp(self):self.a=Appearance(bytes([30])*384,bytes([30])*624);self.b=Appearance(bytes([170])*384,bytes([170])*624)
    def frame(self,g,t,scene):return g.observe({'ad':True},t,{'ad':scene})
    def first(self,g):
        for t in (0,1):self.assertIsNone(self.frame(g,t,self.a))
        self.assertEqual(self.frame(g,2,self.a),'ad')

    def test_four_ads_sharing_footer_no_longer_consume_one_three_tap_budget(self):
        g=PopupCloser(['ad']);self.first(g)
        for start,scene in ((3,self.b),(11,self.a),(19,self.b)):
            self.assertIsNone(self.frame(g,start,scene))
            self.assertIsNone(self.frame(g,start+.3,scene))
            self.assertIsNone(self.frame(g,start+.6,scene))
            self.assertEqual(self.frame(g,start+7,scene),'ad')
        self.assertEqual(g.total,4);self.assertEqual(g.attempts,1);self.assertEqual(g.changed,3)

    def test_unchanged_popup_still_stops_after_three_attempts(self):
        g=PopupCloser(['ad']);self.first(g)
        self.assertEqual(self.frame(g,10,self.a),'ad');self.assertEqual(self.frame(g,18,self.a),'ad')
        with self.assertRaises(SafetyStop):self.frame(g,26,self.a)

    def test_candidate_change_must_be_stable_before_reset_or_retry(self):
        g=PopupCloser(['ad']);self.first(g)
        for t in (10,11,12):
            before=g.attempts
            self.assertIsNone(self.frame(g,t,self.b))
            self.assertEqual(g.attempts,before)
            if t<12:self.frame(g,t+.1,self.a)
        self.assertEqual(g.changed,0)

    def test_eight_second_gap_survives_confirmed_scene_change(self):
        g=PopupCloser(['ad']);self.first(g)
        for t in (3,3.3,3.6,9):self.assertIsNone(self.frame(g,t,self.b))
        self.assertEqual(self.frame(g,10,self.b),'ad');self.assertEqual(g.total,2)

    def test_new_scene_on_fourth_attempt_is_debounced_instead_of_premature_stop(self):
        g=PopupCloser(['ad']);self.first(g);self.frame(g,10,self.a);self.frame(g,18,self.a)
        self.assertIsNone(self.frame(g,26,self.b));self.assertIsNone(self.frame(g,26.3,self.b))
        self.assertEqual(self.frame(g,26.6,self.b),'ad');self.assertEqual(g.attempts,1)

    def test_animation_or_cycling_scenes_cannot_bypass_global_twelve_taps(self):
        g=PopupCloser(['ad']);self.first(g)
        for n in range(1,12):
            t=2+8*n;scene=self.b if n%2 else self.a
            self.frame(g,t-2,scene);self.frame(g,t-1,scene)
            self.assertEqual(self.frame(g,t,scene),'ad')
        self.frame(g,96,self.a);self.frame(g,97,self.a)
        with self.assertRaisesRegex(SafetyStop,'12회'):self.frame(g,98,self.a)
        self.assertEqual(g.total,12)

    def test_unstable_changed_content_is_bounded_by_time(self):
        g=PopupCloser(['ad']);self.first(g)
        with self.assertRaisesRegex(SafetyStop,'180초'):self.frame(g,180,self.b)

    def test_absence_resets_chain_and_diagnostics_never_contain_pixels(self):
        g=PopupCloser(['ad']);self.first(g)
        for t in (3,4,5):g.observe({},t)
        self.assertFalse(g.hold);self.assertEqual(g.total,0);self.assertIsNone(g.chain_since)
        self.assertNotIn('title',g.diagnostics());self.assertEqual(g.diagnostics()['attempts'],0)
