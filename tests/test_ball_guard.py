import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from ball_guard import BallGuard,halt_for_balls,parse_balance
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
            self.assertTrue(g.observe(png,15,stop));self.assertIn('확인 실패',g.reason)

    def test_stop_precedes_notice_and_dry_disabled_never_send(self):
        for dry,enabled in ((False,True),(True,True),(False,False)):
            with tempfile.TemporaryDirectory() as root:
                app=Mock();app.stop=threading.Event();g=Mock(reason='볼 구매 화면 감지')
                app.dispatch_notification.side_effect=lambda *args:self.assertTrue(app.stop.is_set())
                cfg=dict(dry=dry,notifications=dict(enabled=enabled))
                halt_for_balls(app,cfg,g,b'png',root)
                self.assertTrue(app.stop.is_set());self.assertTrue(cfg['_halted_for_balls'])
                self.assertEqual(app.dispatch_notification.call_count,int(enabled and not dry))
                self.assertEqual((Path(root)/'logs/ball_stop.png').read_bytes(),b'png')

    def test_real_session_stops_before_recovery_reward_or_tap(self):
        with tempfile.TemporaryDirectory() as root:
            app=App.__new__(App);app.stop=threading.Event();app.emit=Mock();app.dispatch_notification=Mock()
            app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
            button=new_button('play');button.update(id='play',enabled=True,template='play.png')
            png=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
            adb=Mock();adb.screenshot.return_value=png
            g=Mock(hold=True,reason='볼 구매 화면 감지');g.observe.side_effect=[False,True]
            matcher=Mock();matcher.find.return_value=(True,(1,1),1.,(720,1280))
            cfg=dict(buttons=[button],dry=False,notifications=dict(enabled=False),adb='adb',serial='device',_scheduled=True)
            with patch('app.ROOT',Path(root)),patch('app.BallGuard',return_value=g),patch('app.ADB',return_value=adb),\
                    patch('vision.Matcher',return_value=matcher),patch('app.LeagueBonus') as bonus:
                app.run_session(cfg)
            self.assertTrue(app.stop.is_set());self.assertTrue(cfg['_halted_for_balls'])
            adb.tap.assert_not_called();adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()
            bonus.return_value.observe.assert_not_called();self.assertEqual(adb.screenshot.call_count,2)
