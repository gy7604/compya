"""Resume an existing purchase dialog through the real worker boundary."""
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import Mock,patch
from app import App
from ranking_schedule import DailyRun,defaults,KST
from test_ranking import fixture


class StartupPaymentTests(unittest.TestCase):
    def test_worker_verifies_fresh_price_and_persists_intent_before_resumed_tap(self):
        for pending in (None,'star'):
            with self.subTest(pending=pending),tempfile.TemporaryDirectory() as root:
                records,ranking=fixture();ranking['allow_star']=True
                schedule=dict(defaults(),ocr_prices=True,completion_notice=False)
                daily=DailyRun(root,schedule,lambda:datetime(2026,11,7,21,tzinfo=KST));daily.begin()
                daily.entry.update(completed=10,pending=pending);daily.save()
                app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
                app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
                adb=Mock();adb.screenshot.return_value=b'frame'
                def tap(point):
                    self.assertEqual(daily.entry['pending'],'star')
                    self.assertEqual(daily.entry['last_payment'],dict(currency='star',amount=80))
                    reloaded=DailyRun(root,schedule,daily.clock)
                    self.assertEqual(reloaded.entry['pending'],'star')
                    self.assertEqual(reloaded.entry['paid_remaining'],2)
                    self.assertEqual(point,(21,20))
                    app.stop.set()
                adb.tap.side_effect=tap
                def matcher(path,*args,**kwargs):
                    key=path.removesuffix('.png');obj=Mock()
                    obj.find.return_value=(key in ('refresh_dialog','star_confirm'),(100,200),.99,(720,1280))
                    return obj
                with patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),patch('app.time.monotonic',side_effect=range(1000)),patch('price_ocr.PriceReader.verify',return_value=(80,3,12)) as verify:
                    app.run_session(dict(buttons=records,rules=[],ranking=ranking,games=100,dry=False,adb='adb',serial='device',schedule=schedule,_daily=daily))
                if pending:
                    verify.assert_not_called();adb.tap.assert_not_called()
                    self.assertEqual(daily.entry['pending'],pending)
                else:
                    verify.assert_called_once();adb.tap.assert_called_once()
                    self.assertEqual(daily.entry['completed'],10)
