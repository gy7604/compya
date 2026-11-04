import json
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from ball_rest import BallRest,KST
from ball_balance import BallBalanceReader,parse_stock,separate_stock

ROOT=Path(__file__).resolve().parents[1]


class StockTests(unittest.TestCase):
    def test_capacity_is_not_the_match_cost(self):
        for text,expected in [('4/120',(4,120)),('5 120',(5,120)),('0 / 120',(0,120)),('150/120',(150,120))]:
            self.assertEqual(parse_stock(text),expected)
        for text in ('3/5','5','5/0','-5/120','P5/120','5/120 5','5/하20','5/12','5.0/120','51/20'):
            self.assertIsNone(parse_stock(text),text)

    def test_shortage_and_uncertain_results_retry_after_five_minutes(self):
        for stock in ((0,120),(4,120),None,(-1,120),(True,120),(5,5)):
            with self.subTest(stock=stock),tempfile.TemporaryDirectory() as root:
                now=[datetime(2026,11,4,9,tzinfo=KST)];r=BallRest(root,clock=lambda:now[0]);r.begin('부족')
                self.assertFalse(r.submit(stock));self.assertTrue(r.active)
                now[0]+=timedelta(seconds=299);self.assertFalse(r.due())
                now[0]+=timedelta(seconds=1);self.assertTrue(r.due())

    def test_two_equal_five_ball_readings_confirm_after_one_point_five_seconds(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,4,9,tzinfo=KST)];r=BallRest(root,clock=lambda:now[0]);r.begin('부족')
            self.assertFalse(r.submit((5,120)));self.assertEqual(r.next_check-now[0],timedelta(seconds=1.5))
            self.assertFalse(r.submit((5,120)));self.assertTrue(r.active)
            now[0]+=timedelta(seconds=1.5);self.assertTrue(r.submit((5,120)))

    def test_changed_or_missing_confirmation_does_not_resume(self):
        for second in ((4,120),None,(6,120),(5,150)):
            with self.subTest(second=second),tempfile.TemporaryDirectory() as root:
                now=[datetime(2026,11,4,9,tzinfo=KST)];r=BallRest(root,clock=lambda:now[0]);r.begin('부족');r.submit((5,120))
                now[0]+=timedelta(seconds=1.5);self.assertFalse(r.submit(second));self.assertTrue(r.active)

    def test_confirmation_status_does_not_describe_a_minute_long_wait(self):
        with tempfile.TemporaryDirectory() as root:
            r=BallRest(root);r.begin('부족');r.submit((5,120));r.events.clear();r.tick()
            self.assertIn('즉시 재확인 중',r.events[-1]);self.assertNotIn('1분',r.events[-1])

    def test_stale_confirmation_and_restart_require_new_proof(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,4,9,tzinfo=KST)];clock=lambda:now[0]
            r=BallRest(root,clock=clock);r.begin('부족');r.submit((5,120))
            now[0]+=timedelta(seconds=31);self.assertFalse(r.submit((5,120)));self.assertTrue(r.active)
            r=BallRest(root,clock=clock);now[0]+=timedelta(seconds=1.5)
            self.assertFalse(r.submit((5,120)));now[0]+=timedelta(seconds=1.5)
            self.assertTrue(r.submit((5,120)))

    def test_legacy_26_minute_pause_becomes_a_stock_check_even_if_deadline_passed(self):
        for until in ('2026-11-04T08:00:00+09:00','2026-11-04T12:00:00+09:00'):
            with tempfile.TemporaryDirectory() as root:
                (Path(root)/'ball_rest.json').write_text(json.dumps({'until':until}))
                r=BallRest(root,clock=lambda:datetime(2026,11,4,9,tzinfo=KST))
                self.assertTrue(r.active);self.assertTrue(r.due());r.tick();self.assertTrue(r.active)

    def test_reading_requires_icon_and_registered_lobby(self):
        reader=BallBalanceReader(ROOT);image=np.zeros((1280,720,3),np.uint8)
        marker=cv2.imread(str(ROOT/'assets/ball_balance_icon.png'));h,w=marker.shape[:2]
        image[84:84+h,500:500+w]=marker;png=cv2.imencode('.png',image)[1].tobytes()
        self.assertFalse(reader.visible(png))
        lobby=cv2.imread(str(ROOT/'assets/league_next_match.png'));h,w=lobby.shape[:2];image[580:580+h,200:200+w]=lobby
        png=cv2.imencode('.png',image)[1].tobytes();self.assertTrue(reader.visible(png))
        image[84:121,500:537]=0;self.assertFalse(reader.visible(cv2.imencode('.png',image)[1].tobytes()))

    def test_two_preprocessing_variants_must_agree_and_stop_cancels(self):
        reader=BallBalanceReader(ROOT);reader.visible=Mock(return_value=True)
        png=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
        with patch('ball_balance.separate_stock',return_value=np.zeros((48,108,3),np.uint8)):
            with patch.object(reader,'ocr',side_effect=[(5,120),(4,120)]):self.assertIsNone(reader.read(png,threading.Event()))
            with patch.object(reader,'ocr',side_effect=[(5,120),(5,120)]):self.assertEqual(reader.read(png,threading.Event()),(5,120))
            stop=threading.Event()
            def cancel(*args):stop.set();return (5,120)
            with patch.object(reader,'ocr',side_effect=cancel) as ocr:
                self.assertIsNone(reader.read(png,stop));self.assertEqual(ocr.call_count,1)
            with patch.object(reader,'ocr',side_effect=subprocess.TimeoutExpired('ocr',8)):self.assertIsNone(reader.read(png,threading.Event()))

    def test_a_blank_separator_is_not_invented_into_a_readable_ratio(self):
        self.assertIsNone(separate_stock(np.zeros((48,108,3),np.uint8)))
