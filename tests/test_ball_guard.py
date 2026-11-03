import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from ball_guard import BallGuard,parse_balance
from ball_rest import BallRest,begin_ball_rest,KST
from datetime import datetime,timedelta
from app import App
from registry import new_button

ROOT=Path(__file__).resolve().parents[1]


def frame(guard,indexes):
    image=np.zeros((1280,720,3),np.uint8)
    for index in indexes:
        m=guard.matchers[index];x,y,_,_=m.roi;h,w=m.template.shape
        image[y:y+h,x:x+w]=m.color_template
    return cv2.imencode('.png',image)[1].tobytes()


class BallTests(unittest.TestCase):
    def test_balance_is_a_ratio_not_arbitrary_digits(self):
        self.assertEqual(parse_balance('3 / 5'),(3,5));self.assertEqual(parse_balance('0/5'),(0,5))
        for text in ('35','-3/5','3/0','3/5 10','3/50','P 3/5'):
            self.assertIsNone(parse_balance(text))

    def test_shop_requires_two_distinct_markers_and_two_frames(self):
        g=BallGuard(ROOT);stop=threading.Event()
        for indexes in ([0],[1],[]):
            self.assertFalse(g.observe(frame(g,indexes),0,stop));self.assertFalse(g.hold)
        png=frame(g,[0,1]);self.assertFalse(g.observe(png,1,stop));self.assertTrue(g.hold)
        self.assertTrue(g.observe(png,1.2,stop));self.assertIn('구매 화면',g.reason)

    def test_insufficient_result_requires_two_matching_ratios(self):
        g=BallGuard(ROOT);stop=threading.Event();png=frame(g,[2,3])
        with patch.object(g,'read',side_effect=[(3,5),(2,5),(2,5)]):
            self.assertFalse(g.observe(png,0,stop));self.assertTrue(g.hold)
            self.assertFalse(g.observe(png,1,stop));self.assertTrue(g.observe(png,2,stop))
        self.assertIn('보유 2 / 필요 5',g.reason)

    def test_ball_item_info_also_stops_before_product_selection(self):
        g=BallGuard(ROOT);stop=threading.Event();png=frame(g,[4,5])
        self.assertFalse(g.observe(png,0,stop));self.assertTrue(g.hold)
        self.assertTrue(g.observe(png,1,stop));self.assertIn('구매 화면',g.reason)

    def test_enough_balls_and_other_screens_do_not_stop(self):
        g=BallGuard(ROOT);stop=threading.Event()
        with patch.object(g,'read',return_value=(5,5)):
            for now in (0,1,2):self.assertFalse(g.observe(frame(g,[2,3]),now,stop));self.assertFalse(g.hold)
        png=cv2.imencode('.png',np.zeros((1920,1080,3),np.uint8))[1].tobytes()
        self.assertFalse(g.observe(png,3,stop))
        stop.set();self.assertFalse(g.observe(frame(g,[0,1]),4,stop))

    def test_unreadable_disabled_result_is_bounded_without_taps(self):
        g=BallGuard(ROOT);png=frame(g,[2,3]);stop=threading.Event()
        with patch.object(g,'read',return_value=None):
            self.assertFalse(g.observe(png,0,stop));self.assertFalse(g.observe(png,14,stop))
            self.assertTrue(g.observe(png,15,stop));self.assertIn('확인 필요',g.reason)

    def test_pause_keeps_macro_alive_and_dry_disabled_never_send(self):
        for dry,enabled in ((False,True),(True,True),(False,False)):
            with tempfile.TemporaryDirectory() as root:
                app=Mock();app.stop=threading.Event();g=Mock(reason='볼 구매 화면 감지')
                cfg=dict(dry=dry,notifications=dict(enabled=enabled))
                rest=BallRest(root,dry=dry)
                begin_ball_rest(app,cfg,g,rest,b'png',root)
                begin_ball_rest(app,cfg,g,rest,b'png',root)
                self.assertFalse(app.stop.is_set());self.assertTrue(rest.active)
                self.assertEqual(app.dispatch_notification.call_count,int(enabled and not dry))
                self.assertEqual((Path(root)/'logs/ball_wait.png').read_bytes(),b'png')

    def test_session_waits_26_minutes_without_restart_then_resumes(self):
        with tempfile.TemporaryDirectory() as root:
            app=App.__new__(App);app.stop=threading.Event();app.emit=Mock();app.dispatch_notification=Mock()
            now=[datetime(2026,11,4,8,tzinfo=KST)]
            def wait(seconds):now[0]+=timedelta(seconds=max(60,seconds));return False
            app.stop.wait=Mock(side_effect=wait)
            app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
            button=new_button('play');button.update(id='play',enabled=True,template='play.png')
            png=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
            adb=Mock();adb.screenshot.return_value=png
            g=Mock(hold=True,reason='볼 구매 화면 감지',layout=None)
            def observe(*args):g.hold=adb.screenshot.call_count<=2;return adb.screenshot.call_count==2
            g.observe.side_effect=observe
            def tap(point):
                self.assertGreaterEqual(now[0],deadline[0]);app.stop.set()
            adb.tap.side_effect=tap
            rest=BallRest(root,clock=lambda:now[0]);deadline=[None]
            original_begin=rest.begin
            def begin(reason):
                result=original_begin(reason);deadline[0]=rest.until;return result
            rest.begin=begin
            matcher=Mock();matcher.find.return_value=(True,(1,1),1.,(720,1280))
            cfg=dict(buttons=[button],dry=False,notifications=dict(enabled=False),adb='adb',serial='device',_scheduled=True)
            with patch('app.ROOT',Path(root)),patch('app.BallGuard',return_value=g),patch('app.ADB',return_value=adb),\
                    patch('vision.Matcher',return_value=matcher),patch('app.BallRest',return_value=rest):
                app.run_session(cfg)
            self.assertTrue(app.stop.is_set());self.assertFalse(rest.active)
            adb.tap.assert_called_once();adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()

    def test_rest_deadline_survives_restart_and_does_not_extend_on_repeated_detection(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,4,8,tzinfo=KST)];r=BallRest(root,clock=lambda:now[0])
            self.assertTrue(r.begin('부족'));until=r.until
            now[0]+=timedelta(minutes=10);self.assertFalse(r.begin('같은 창'));self.assertEqual(r.until,until)
            r=BallRest(root,clock=lambda:now[0]);self.assertTrue(r.active);self.assertFalse(r.tick())
            now[0]=until-timedelta(seconds=1);self.assertFalse(r.tick())
            now[0]=until;self.assertTrue(r.tick());self.assertFalse(r.active)
            self.assertFalse(BallRest(root).active)

    def test_ball_pause_can_yield_to_ranking_without_marking_day_done(self):
        with tempfile.TemporaryDirectory() as root:
            app=App.__new__(App);app.stop=threading.Event();app.emit=Mock();app.registry=Mock()
            app.registry.resolve.side_effect=lambda p:p
            button=new_button('play');button.update(id='play',enabled=True,template='play.png')
            png=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
            adb=Mock();adb.screenshot.return_value=png
            g=Mock(hold=True,reason='볼 부족',layout=None);g.observe.return_value=True
            matcher=Mock();matcher.find.return_value=(False,(1,1),0.,(720,1280))
            cfg=dict(buttons=[button],dry=False,notifications=dict(enabled=False),adb='adb',serial='device',_scheduled=True,_handoff=lambda:True)
            with patch('app.ROOT',Path(root)),patch('app.BallGuard',return_value=g),patch('app.ADB',return_value=adb),patch('vision.Matcher',return_value=matcher):
                app.run_session(cfg)
            self.assertTrue(cfg.get('_yielded'));self.assertFalse(app.stop.is_set())
            self.assertNotIn('_ranking_done',cfg);self.assertTrue(BallRest(root).active)
            adb.tap.assert_not_called()

    def test_close_points_are_registered_free_controls_and_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            r=BallRest(root,dry=True);r.begin('부족')
            self.assertIsNone(r.close_point('unknown',0));self.assertEqual(r.close_point('info',0),(610,287))
            self.assertIsNone(r.close_point('info',1));self.assertEqual(r.close_point('info',5),(610,287))
            self.assertEqual(r.close_point('info',10),(610,287));self.assertIsNone(r.close_point('info',15))
