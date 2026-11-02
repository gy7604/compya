import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from engine import SafetyStop
from price_ocr import PriceReader,parse_price,parse_payment,price_crop
from ranking_schedule import DailyRun,defaults,guard_action,KST
from ranking import RankingMachine
from datetime import datetime
from test_ranking import fixture


class PriceTests(unittest.TestCase):
    def test_payment_count_is_positive_evidence_not_a_guessed_limit(self):
        self.assertEqual(parse_payment('20,000 10/12'),(20000,10,12))
        self.assertEqual(parse_payment('30 0/12'),(30,0,12))
        for text in ('7000','7000 13/12','7000 1/0','7000 11 /12','7000 11/12 2/3'):
            with self.assertRaises(SafetyStop):parse_payment(text)

    def test_strict_numeric_prices_allow_changes_but_reject_unclear_text(self):
        for text,amount in [('3,000',3000),('7,000',7000),('20 000',20000),('10000',10000),('30',30)]:
            self.assertEqual(parse_price(text),amount)
        for text in ('','0','-3','1 00','7,00','P 3000','3OOO','3000 7000','1,000,000,000'):
            with self.assertRaises(SafetyStop):parse_price(text)

    def test_currency_must_be_inside_registered_button_and_resolution(self):
        png=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
        price_crop(png,(318,805),(360,805),(720,1280))
        with self.assertRaises(SafetyStop):price_crop(png,(40,50),(360,805),(720,1280))
        with self.assertRaises(SafetyStop):price_crop(png,(318,805),(360,805),(1080,1920))

    def setup_verifier(self):
        reader=PriceReader('missing-test-helper.exe');reader.read=Mock(return_value=7000)
        adb=Mock();adb.screenshot.return_value=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
        matchers={key:Mock() for key in ('currency','dialog','other')}
        for key in ('currency','dialog'):matchers[key].find.return_value=(True,(318,805),.99,(720,1280))
        matchers['other'].find.return_value=(False,(0,0),.2,(720,1280))
        stop=threading.Event();stop.wait=Mock(return_value=False)
        args=(adb,stop,lambda:True,'currency','dialog','other',matchers,dict(click_point=[360,805]),(720,1280))
        return reader,adb,stop,matchers,args

    def test_requires_three_screens_and_two_equal_readings_each(self):
        reader,adb,_,_,args=self.setup_verifier()
        self.assertEqual(reader.verify(*args),7000)
        self.assertEqual(adb.screenshot.call_count,3)
        self.assertEqual(reader.read.call_count,6)
        adb.tap.assert_not_called()

    def test_disagreement_currency_change_or_time_change_never_authorizes(self):
        reader,adb,_,_,args=self.setup_verifier()
        reader.read.side_effect=[7000,1000,7000,7000,7000,7000]
        with self.assertRaises(SafetyStop):reader.verify(*args)
        reader,adb,_,matchers,args=self.setup_verifier()
        matchers['other'].find.return_value=(True,(318,805),.99,(720,1280))
        with self.assertRaises(SafetyStop):reader.verify(*args)
        reader.read.assert_not_called()
        args=list(args);args[2]=lambda:False
        with self.assertRaises(SafetyStop):reader.verify(*args)

    def test_manual_stop_skips_ocr_and_no_taps(self):
        reader,adb,stop,_,args=self.setup_verifier();stop.set()
        self.assertIsNone(reader.verify(*args))
        reader.read.assert_not_called();adb.tap.assert_not_called()

    def test_native_helper_failure_is_closed(self):
        with tempfile.TemporaryDirectory() as root:
            helper=Path(root)/'PriceOCR.exe';helper.touch()
            reader=PriceReader(helper)
            with patch('price_ocr.subprocess.run',return_value=Mock(returncode=1)):
                with self.assertRaises(SafetyStop):reader.read(np.zeros((10,10,3),np.uint8),root,0)

    def test_ocr_receipt_is_fresh_single_use_and_saved_before_payment(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,1)
        settings=dict(defaults(),ocr_prices=True)
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,settings,lambda:datetime(2026,11,2,13,tzinfo=KST));daily.begin()
            with patch('ranking_schedule.time.monotonic',return_value=100):
                with self.assertRaises(SafetyStop):guard_action(daily,machine,'points_confirm',{},settings)
                daily.ocr_payment=('points_confirm',7000,96,11,12)
                with self.assertRaises(SafetyStop):guard_action(daily,machine,'points_confirm',{},settings)
                daily.ocr_payment=('points_confirm',7000,100,11,12)
                self.assertEqual(guard_action(daily,machine,'points_confirm',{},settings),'points_confirm')
                self.assertIsNone(daily.ocr_payment)
                self.assertEqual(daily.entry['last_payment'],dict(currency='points',amount=7000))
                self.assertEqual(daily.entry['pending'],'points')
                self.assertEqual(daily.entry['paid_remaining'],10)
