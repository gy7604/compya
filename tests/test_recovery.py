import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from recovery import *
from registry import new_button
from adb import ADB


def fixture():
    records=[]
    for key in ['title','menu','goal','ad','ad2']:
        r=new_button(key);r.update(id=key,enabled=True,kind='condition',template=key+'.png',click_mode='fixed',click_point=[100,200],click_size=[720,1280]);records.append(r)
    cfg=default_recovery();cfg.update(enabled=True,goal='goal',steps=['title','menu'],popups=['ad','ad2'])
    return records,cfg

def visible(*keys):return dict.fromkeys(keys,True)

class RecoveryTests(unittest.TestCase):
    def machine(self):
        records,cfg=fixture();return RecoveryMachine(cfg,records)
    def frames(self,m,found,start):return [m.observe(found,start+i) for i in range(3)]
    def test_process_absence_requires_two_checks_and_restart_is_bounded(self):
        m=self.machine()
        self.assertFalse(m.poll(False,0));self.assertTrue(m.hold)
        self.assertTrue(m.poll(False,2));self.assertEqual(m.launches,1)
        self.assertFalse(m.poll(False,20));self.assertTrue(m.poll(False,32));self.assertTrue(m.poll(False,62))
        with self.assertRaises(SafetyStop):m.poll(False,92)
    def test_route_and_popups_return_to_goal(self):
        m=self.machine();m.poll(False,0);m.poll(False,2);m.poll(True,3)
        self.assertEqual(self.frames(m,visible('title'),3),[None,None,'title'])
        self.assertEqual(self.frames(m,visible('ad'),6),[None,None,'ad'])
        for t in range(9,15):self.assertIsNone(m.observe(visible('ad','goal'),t));self.assertFalse(m.restored)
        self.frames(m,visible('menu'),15)
        # Interrupted title tap is restored; next step must be confirmed before advancing.
        for t in range(18,24):
            action=m.observe(visible('menu'),t)
            if action:self.assertEqual(action,'menu')
        self.frames(m,visible('goal'),24)
        self.assertTrue(m.restored);self.assertFalse(m.active)
    def test_ad_alone_preempts_goal_and_never_repeats_close_while_visible(self):
        m=self.machine()
        self.assertEqual(self.frames(m,visible('ad','goal'),0),[None,None,'ad'])
        for t in range(3,12):self.assertIsNone(m.observe(visible('ad','goal'),t));self.assertFalse(m.restored)
        self.frames(m,visible('goal'),12);m.observe(visible('goal'),15)
        self.assertTrue(m.restored)
    def test_live_game_does_not_trigger_recovery_or_launch(self):
        m=self.machine()
        for t in range(9):self.assertIsNone(m.observe(visible('goal'),t));self.assertFalse(m.hold)
        self.assertFalse(m.poll(True,10));self.assertEqual(m.launches,0)
    def test_title_screen_without_process_exit_can_recover(self):
        m=self.machine();self.assertEqual(self.frames(m,visible('title'),0),[None,None,'title'])
        self.assertTrue(m.hold);self.assertTrue(m.active)
    def test_missing_image_and_duplicate_route_rejected_at_start_but_draft_saves(self):
        records,cfg=fixture()
        for changes in [dict(goal=''),dict(steps=[]),dict(steps=['title','title']),dict(popups=['missing'])]:
            bad=dict(cfg,**changes)
            with self.assertRaises(ValueError):validate_recovery(bad,records)
            with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
                store=RecoveryStore(folder);store.save(bad);self.assertEqual(store.load(),bad)
        records[-1]['click_mode']='center'
        with self.assertRaises(ValueError):validate_recovery(cfg,records)
    def test_package_validation_rejects_shell_text(self):
        for value in ['bad; reboot','x && am start','../app','a b']:
            with self.assertRaises(ValueError):validate_draft(dict(default_recovery(),package=value))
    def test_adb_queries_and_launch_target_only_configured_package(self):
        adb=ADB('adb','emulator-5554');adb.run=Mock(return_value=b'1234\n')
        self.assertTrue(adb.app_running(PACKAGE));self.assertIn(PACKAGE,adb.run.call_args.args[-1])
        adb.run=Mock(side_effect=[(PACKAGE+'/com.hive.HiveUnityPlayerActivity\n').encode(),b'Status: ok'])
        adb.launch_app(PACKAGE)
        self.assertEqual(adb.run.call_args.args,('shell','am','start','-n',PACKAGE+'/com.hive.HiveUnityPlayerActivity'))
        adb.run=Mock(return_value=b'No activity found')
        with self.assertRaises(RuntimeError):adb.launch_app(PACKAGE)
    def test_runner_relaunch_ads_route_and_preserves_completed_batch_count(self):
        import threading
        import cv2
        import numpy as np
        from unittest.mock import patch
        from app import App
        from test_ranking import fixture as ranking_fixture
        records,rank_cfg=ranking_fixture();extra,recovery_cfg=fixture()
        records.extend(r for r in extra if r['id']!='goal');recovery_cfg['goal']='lobby'
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False);app.emit=Mock()
        app.registry=Mock();app.registry.resolve.side_effect=lambda path:path
        state=dict(frame=-1,launched=False)
        png=cv2.imencode('.png',np.zeros((1280,720,3),dtype=np.uint8))[1].tobytes()
        adb=Mock()
        def screenshot():state['frame']+=1;return png
        adb.screenshot.side_effect=screenshot
        adb.app_running.side_effect=lambda package:state['frame']<17 or state['launched']
        adb.launch_app.side_effect=lambda package:state.update(launched=True)
        def found(key,frame):
            if frame<3:return key in ['lobby','series','refresh']
            if frame<6:return key=='proceed'
            if frame<9:return False
            if frame<15:return key in ['result','confirm']
            if frame<18:return key in ['lobby','series']
            if frame<21:return key=='title'
            if frame<24:return key=='ad'
            if frame<30:return key=='menu'
            if frame<39:return key in ['lobby','series']
            if frame<44:return key=='proceed'
            if frame<49:return False
            if frame<60:return key in ['result','confirm']
            raise RuntimeError('test exceeded route')
        def matcher(path,*args,**kwargs):
            key=path.removesuffix('.png');m=Mock();m.find.side_effect=lambda image:(found(key,state['frame']),(100,200),.99,(720,1280));return m
        with patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),patch('app.time.monotonic',side_effect=range(10000)):
            app.run(dict(buttons=records,rules=[],ranking=rank_cfg,recovery=recovery_cfg,games=2,dry=False,adb='adb',serial='device'))
        adb.launch_app.assert_called_once_with(PACKAGE)
        logs=[call.args[0] for call in app.emit.call_args_list]
        self.assertTrue(any('완료 1/2회 유지' in log for log in logs),logs[-10:])
        self.assertTrue(any('완료 2/2회' in log for log in logs),logs[-10:])
        self.assertFalse(any('안전 중지' in log for log in logs),logs[-10:])
    def test_runner_dry_mode_never_launches_or_taps_when_game_is_closed(self):
        import threading
        from unittest.mock import patch
        from app import App
        from test_ranking import fixture as ranking_fixture
        records,rank_cfg=ranking_fixture();extra,recovery_cfg=fixture()
        records.extend(r for r in extra if r['id']!='goal');recovery_cfg.update(goal='lobby',max_launches=1)
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False);app.emit=Mock()
        app.registry=Mock();app.registry.resolve.side_effect=lambda path:path
        adb=Mock();adb.app_running.return_value=False
        with patch('app.ADB',return_value=adb),patch('vision.Matcher'),patch('app.time.monotonic',side_effect=range(1000)):
            app.run(dict(buttons=records,rules=[],ranking=rank_cfg,recovery=recovery_cfg,games=2,dry=True,adb='adb',serial='device'))
        adb.launch_app.assert_not_called();adb.tap.assert_not_called();adb.screenshot.assert_not_called()
        self.assertTrue(any('모의 게임 재실행' in call.args[0] for call in app.emit.call_args_list))

    def test_relaunch_can_resume_from_a_later_visible_menu_when_title_was_skipped(self):
        m=self.machine();m.poll(False,0);m.poll(False,2);m.poll(True,3)
        self.assertEqual(self.frames(m,visible('menu'),3),[None,None,'menu'])
        self.assertEqual(m.index,1)
