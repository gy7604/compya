import threading
import unittest
from pathlib import Path
import cv2,numpy as np
from engine import SafetyStop
from patch_download import PatchDownload
ROOT=Path(__file__).resolve().parents[1]
class PatchTests(unittest.TestCase):
    def setUp(self):
        self.guard=PatchDownload(ROOT);self.stop=threading.Event()
        self.png=(ROOT/'tests/fixtures/patch_download_480.png').read_bytes()
    def observe(self,now,png=None,**kw):return self.guard.observe(self.png if png is None else png,now,self.stop,**kw)
    def test_real_fixture_and_scaled_native_resolution(self):
        for size in ((480,854),(720,1280)):
            im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1)
            png=cv2.imencode('.png',cv2.resize(im,size))[1].tobytes()
            title,button,point,actual=self.guard.scan(png)
            self.assertTrue(title);self.assertTrue(button);self.assertEqual(actual,size)
            self.assertLess(abs(point[0]-size[0]*319/480),3)
            self.assertLess(abs(point[1]-size[1]*537/854),3)
    def test_one_confirmation_only_and_no_repeat(self):
        self.assertIsNone(self.observe(0));self.assertTrue(self.guard.hold)
        self.assertIsNone(self.observe(.2));self.assertIsNotNone(self.observe(.4))
        for n in range(1,20):self.assertIsNone(self.observe(n))
    def test_title_without_button_holds_without_click(self):
        im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1);im[505:575,238:400]=0
        png=cv2.imencode('.png',im)[1].tobytes()
        for n in range(5):self.assertIsNone(self.observe(n,png))
        self.assertTrue(self.guard.hold);self.assertFalse(self.guard.confirmed)
    def test_button_without_title_is_not_patch(self):
        im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1);im[278:330,166:326]=0
        png=cv2.imencode('.png',im)[1].tobytes()
        for n in range(5):self.assertIsNone(self.observe(n,png))
        self.assertFalse(self.guard.hold)
    def test_completion_requires_positive_same_screen_three_times(self):
        for n in range(3):self.observe(n)
        blank=cv2.imencode('.png',np.zeros((854,480,3),np.uint8))[1].tobytes()
        for n in range(3,8):self.observe(n,blank)
        self.assertTrue(self.guard.hold)
        self.observe(8,blank,resume=lambda size:'lobby');self.observe(9,blank,resume=lambda size:'intro')
        self.assertTrue(self.guard.hold)
        self.observe(10,blank,resume=lambda size:'intro');self.observe(11,blank,resume=lambda size:'intro')
        self.assertFalse(self.guard.hold);self.assertTrue(self.guard.finished)
        with self.assertRaises(SafetyStop):self.observe(12)
    def test_cancel_and_resolution_safety(self):
        self.stop.set();self.assertIsNone(self.observe(0));self.assertFalse(self.guard.hold)
        self.stop.clear()
        for n in range(2):self.observe(n)
        with self.assertRaises(SafetyStop):self.observe(2,expected_sizes=[(720,1280)])
    def test_timeout_even_when_resolution_changes(self):
        self.observe(0)
        landscape=cv2.imencode('.png',np.zeros((720,1280,3),np.uint8))[1].tobytes()
        self.observe(1799,landscape,resume=lambda size:'lobby');self.assertTrue(self.guard.hold)
        with self.assertRaises(SafetyStop):self.observe(1800,landscape)
    def test_false_frame_breaks_confirm_streak(self):
        self.observe(0);self.observe(1)
        im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1);im[505:575,238:400]=0
        self.observe(2,cv2.imencode('.png',im)[1].tobytes())
        self.assertIsNone(self.observe(3));self.assertIsNone(self.observe(4));self.assertIsNotNone(self.observe(5))
    def test_rotation_breaks_confirmation_and_completion_streaks(self):
        rotation=cv2.imencode('.png',np.zeros((480,854,3),np.uint8))[1].tobytes()
        self.observe(0);self.observe(1);self.observe(2,rotation)
        self.assertIsNone(self.observe(3));self.assertIsNone(self.observe(4));self.assertIsNone(self.observe(5))
        self.assertIsNotNone(self.observe(6))
        blank=cv2.imencode('.png',np.zeros((854,480,3),np.uint8))[1].tobytes()
        for n in (7,8):self.observe(n,blank,resume=lambda size:'lobby')
        self.observe(9,rotation)
        for n in (10,11,12):
            self.observe(n,blank,resume=lambda size:'lobby');self.assertTrue(self.guard.hold)
        self.observe(13,blank,resume=lambda size:'lobby');self.assertFalse(self.guard.hold)

    def test_worker_guard_order_and_dry_click_gate(self):
        source=(ROOT/'app.py').read_text(encoding='utf-8')
        loop=source[source.index('            while not self.stop.is_set():',source.index('def run_session')):]
        self.assertLess(loop.index('patch_download.observe('),loop.index('launch=recovery.poll('))
        self.assertLess(loop.index('patch_download.observe('),loop.index('popup_action=popup_closer.observe('))
        self.assertIn("if not cfg['dry']:adb.tap(patch_point)",loop)
        self.assertLess(loop.index('patch_download.observe('),loop.index('if memory_guard and memory_guard.policy.due(False'))
        self.assertIn("if patch_download.hold:raise SafetyStop",loop)
if __name__=='__main__':unittest.main()

class PatchRunnerTests(unittest.TestCase):
    def test_live_guard_holds_all_other_inputs_and_cleanup_in_both_modes_and_dry(self):
        import tempfile
        from unittest.mock import Mock,patch
        from app import App
        from registry import new_button
        from recovery import default_recovery
        from ranking import default_ranking
        for ranked in (False,True):
            for dry in (False,True):
                with self.subTest(ranked=ranked,dry=dry),tempfile.TemporaryDirectory() as folder:
                    app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
                    app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
                    b=new_button('generic');b.update(id='generic',enabled=True,template='generic.png')
                    ranking=default_ranking()
                    # Simulate scheduled ranking's valid required image references.
                    if ranked:
                        for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','refresh_dialog','points_confirm','star_confirm'):
                            ranking[key]=key
                        ranking['enabled']=True
                    buttons=[b]
                    for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','refresh_dialog','points_confirm','star_confirm'):
                        r=new_button(key);r.update(id=key,enabled=True,template=key+'.png',kind='condition',click_mode='fixed',click_point=[20,20],click_size=[480,854],roi=[0,0,480,854]);buttons.append(r)
                    cfg=dict(buttons=buttons,ranking=ranking,recovery=default_recovery(),dry=dry,adb='adb',serial='device')
                    fixture=(ROOT/'tests/fixtures/patch_download_480.png').read_bytes()
                    blank=cv2.imencode('.png',np.zeros((854,480,3),np.uint8))[1].tobytes()
                    adb=Mock();frames=[fixture]*4+[blank]*3
                    def screenshot():
                        if not frames:app.stop.set();return blank
                        return frames.pop(0)
                    adb.screenshot.side_effect=screenshot
                    matcher=Mock();matcher.find.return_value=(True,(100,200),.99,(480,854))
                    memory=Mock(events=[],changed=False);memory.policy.due.return_value=True
                    guard=PatchDownload(ROOT)
                    with patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),patch('vision.Matcher',return_value=matcher),\
                         patch('patch_download.PatchDownload',return_value=guard),patch('app.create_memory_guard',return_value=memory),\
                         patch('app.run_cleanup') as cleanup,patch('app.RecoveryMachine.poll') as poll,\
                         patch('app.StallWatch') as stall,patch('diagnostics.write_stop_snapshot'):
                        # Unknown post-download frames must not resume just because a generic button matches.
                        with patch.object(guard,'observe',wraps=guard.observe) as observe:
                            # Prevent false completion from mock lobby: only patch guard receives real frames.
                            matcher.find.return_value=(False,(100,200),.1,(480,854))
                            app.run_session(cfg)
                    self.assertTrue(guard.hold,app.emit.call_args_list)
                    self.assertEqual(adb.tap.call_count,0 if dry else 1,app.emit.call_args_list)
                    cleanup.assert_not_called();poll.assert_not_called();adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()
                    stall.return_value.observe.assert_not_called()
                    self.assertFalse(cfg.get('_failed'),app.emit.call_args_list)

    def test_fresh_completion_guard_in_both_modes_and_dry(self):
        import tempfile
        from unittest.mock import Mock,patch
        from app import App
        from registry import new_button
        from recovery import default_recovery
        from ranking import default_ranking
        for ranked in (False,True):
            for dry in (False,True):
                with self.subTest(ranked=ranked,dry=dry),tempfile.TemporaryDirectory() as folder:
                    app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
                    app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
                    b=new_button('generic');b.update(id='generic',enabled=True,template='generic.png')
                    ranking=default_ranking()
                    # Simulate scheduled ranking's valid required image references.
                    if ranked:
                        for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','refresh_dialog','points_confirm','star_confirm'):
                            ranking[key]=key
                        ranking['enabled']=True
                    buttons=[b]
                    for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','refresh_dialog','points_confirm','star_confirm'):
                        r=new_button(key);r.update(id=key,enabled=True,template=key+'.png',kind='condition',click_mode='fixed',click_point=[20,20],click_size=[480,854],roi=[0,0,480,854]);buttons.append(r)
                    cfg=dict(buttons=buttons,ranking=ranking,recovery=default_recovery(),dry=dry,adb='adb',serial='device')
                    fixture=(ROOT/'tests/fixtures/patch_complete_480.png').read_bytes()
                    blank=cv2.imencode('.png',np.zeros((854,480,3),np.uint8))[1].tobytes()
                    adb=Mock();frames=[fixture]*4+[blank]*3
                    def screenshot():
                        if not frames:app.stop.set();return blank
                        return frames.pop(0)
                    adb.screenshot.side_effect=screenshot
                    matcher=Mock();matcher.find.return_value=(True,(100,200),.99,(480,854))
                    memory=Mock(events=[],changed=False);memory.policy.due.return_value=True
                    guard=PatchDownload(ROOT)
                    with patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),patch('vision.Matcher',return_value=matcher),\
                         patch('patch_download.PatchDownload',return_value=guard),patch('app.create_memory_guard',return_value=memory),\
                         patch('app.run_cleanup') as cleanup,patch('app.RecoveryMachine.poll') as poll,\
                         patch('app.StallWatch') as stall,patch('diagnostics.write_stop_snapshot'):
                        # Unknown post-download frames must not resume just because a generic button matches.
                        with patch.object(guard,'observe',wraps=guard.observe) as observe:
                            # Prevent false completion from mock lobby: only patch guard receives real frames.
                            matcher.find.return_value=(False,(100,200),.1,(480,854))
                            app.run_session(cfg)
                    self.assertTrue(guard.hold,app.emit.call_args_list)
                    self.assertEqual(adb.tap.call_count,0 if dry else 1,app.emit.call_args_list)
                    cleanup.assert_not_called();poll.assert_not_called();adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()
                    stall.return_value.observe.assert_not_called()
                    self.assertFalse(cfg.get('_failed'),app.emit.call_args_list)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.guard=PatchDownload(ROOT);self.stop=threading.Event()
        self.png=(ROOT/'tests/fixtures/patch_complete_480.png').read_bytes()
    def observe(self,now,png=None,**kw):return self.guard.observe(self.png if png is None else png,now,self.stop,**kw)
    def test_fresh_complete_native_and_normalized_one_shot(self):
        for size in ((480,854),(720,1280)):
            self.guard=PatchDownload(ROOT)
            im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1)
            png=cv2.imencode('.png',cv2.resize(im,size))[1].tobytes()
            self.assertIsNone(self.observe(0,png));self.assertIsNone(self.observe(1,png))
            point=self.observe(2,png);self.assertLess(abs(point[0]-size[0]/2),2)
            self.assertTrue(self.guard.started);self.assertFalse(self.guard.confirmed)
            for n in range(3,10):self.assertIsNone(self.observe(n,png))
            self.assertTrue(self.guard.hold)
    def test_missing_button_never_clicks_and_title_holds(self):
        im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1);im[778:844,75:405]=0
        png=cv2.imencode('.png',im)[1].tobytes()
        for n in range(5):self.assertIsNone(self.observe(n,png))
        self.assertTrue(self.guard.hold);self.assertFalse(self.guard.started)
    def test_missing_title_button_alone_never_clicks(self):
        im=cv2.imdecode(np.frombuffer(self.png,np.uint8),1);im[750:784,130:365]=0
        png=cv2.imencode('.png',im)[1].tobytes()
        for n in range(5):self.assertIsNone(self.observe(n,png))
        self.assertFalse(self.guard.hold);self.assertFalse(self.guard.started)
    def test_initial_download_then_completion_uses_separate_streak_and_budget(self):
        initial=(ROOT/'tests/fixtures/patch_download_480.png').read_bytes()
        self.observe(0,initial);self.observe(1,initial)
        self.assertIsNotNone(self.observe(2,initial))
        self.assertIsNone(self.observe(3));self.assertIsNone(self.observe(4));self.assertIsNotNone(self.observe(5))
        self.assertTrue(self.guard.started);self.assertTrue(self.guard.confirmed)
        self.assertEqual(self.guard.last_action,'patch_complete_start')
    def test_fresh_completion_waits_for_registered_route_and_rejects_reappearance(self):
        for n in range(3):self.observe(n)
        blank=cv2.imencode('.png',np.zeros((854,480,3),np.uint8))[1].tobytes()
        for n in range(3,6):self.observe(n,blank)
        self.assertTrue(self.guard.hold)
        for n in range(6,9):self.observe(n,blank,resume=lambda size:'ranking_lobby')
        self.assertFalse(self.guard.hold)
        with self.assertRaises(SafetyStop):self.observe(9)
    def test_completion_cancel_resolution_and_timeout(self):
        self.stop.set();self.assertIsNone(self.observe(0));self.assertFalse(self.guard.hold)
        self.stop.clear();self.observe(1);self.observe(2)
        with self.assertRaises(SafetyStop):self.observe(3,expected_sizes=[(720,1280)])
        self.guard=PatchDownload(ROOT);self.observe(0)
        with self.assertRaises(SafetyStop):self.observe(1800)
    def test_completion_rotation_resets_streak(self):
        self.observe(0);self.observe(1)
        rotation=cv2.imencode('.png',np.zeros((480,854,3),np.uint8))[1].tobytes()
        self.observe(2,rotation)
        for n in (3,4,5):self.assertIsNone(self.observe(n))
        self.assertIsNotNone(self.observe(6))
    def test_ambiguous_initial_and_completion_titles_fail_closed(self):
        initial=cv2.imdecode(np.frombuffer((ROOT/'tests/fixtures/patch_download_480.png').read_bytes(),np.uint8),1)
        complete=cv2.imdecode(np.frombuffer(self.png,np.uint8),1)
        initial[750:850]=complete[750:850]
        png=cv2.imencode('.png',initial)[1].tobytes()
        with self.assertRaises(SafetyStop):self.observe(0,png)
        self.assertFalse(self.guard.started);self.assertFalse(self.guard.confirmed)


class PatchPopupReturnTests(unittest.TestCase):
    def test_footer_requires_real_close_shape_then_existing_popup_closer_runs(self):
        from unittest.mock import Mock
        from test_popup_chain import ad_image,png,RECORD
        from patch_download import registered_popup_return
        from popup_appearance import read_appearances
        from interruptions import PopupCloser
        records={'ad':dict(RECORD,click_point=[78,1226])}
        matcher=Mock();matcher.find.return_value=(True,(230,1230),.99,(720,1280))
        guard=PatchDownload(ROOT);stop=threading.Event()
        complete=(ROOT/'tests/fixtures/patch_complete_480.png').read_bytes()
        for n in range(3):guard.observe(complete,n,stop)
        for n in range(3,8):
            frame=png(ad_image(close=False))
            guard.observe(frame,n,stop,lambda size:registered_popup_return(frame,records,{'ad':matcher},['ad']))
        self.assertTrue(guard.hold)
        frame=png(ad_image())
        for n in range(8,11):guard.observe(frame,n,stop,lambda size:registered_popup_return(frame,records,{'ad':matcher},['ad']))
        self.assertFalse(guard.hold);self.assertTrue(guard.finished)
        closer=PopupCloser(['ad']);appearance=read_appearances(frame,records,{'ad':True})
        actions=[closer.observe({'ad':True},n,appearance) for n in (11,12,13)]
        self.assertEqual(actions,[None,None,'ad'])
    def test_only_registered_fixed_matched_popups_qualify(self):
        from unittest.mock import Mock
        from patch_download import registered_popup_return
        matcher=Mock();matcher.find.return_value=(True,(1,1),.99,(720,1280))
        records={'generic':dict(click_mode='fixed',roi=[1,1,40,40]),'ad':dict(click_mode='fixed',roi=[1,1,40,40])}
        self.assertIsNone(registered_popup_return(b'frame',records,{'generic':matcher},['ad']))
        self.assertEqual(registered_popup_return(b'frame',records,{'ad':matcher},['ad']),'ad')
        records['ad']['click_mode']='center'
        self.assertIsNone(registered_popup_return(b'frame',records,{'ad':matcher},['ad']))
        records['ad']['click_mode']='fixed';matcher.find.return_value=(False,(1,1),.1,(720,1280))
        self.assertIsNone(registered_popup_return(b'frame',records,{'ad':matcher},['ad']))
