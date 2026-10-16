import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from notifications import *
from registry import new_button
from app import App

class Tests(unittest.TestCase):
    def fixture(self):
        record=new_button('알림');record.update(id='notice',kind='condition',template='notice.png',enabled=True)
        config=default_notifications();config.update(enabled=True,watch=['notice'],topic='v26_'+('a'*32))
        return [record],config
    def test_debounce_and_rearm(self):
        records,cfg=self.fixture();d=NotificationDetector(cfg,records)
        self.assertEqual([d.observe({'notice':True}) for _ in range(5)],[[],[],['notice'],[],[]])
        d.observe({});d.observe({});self.assertEqual(d.observe({'notice':True}),[])
        for _ in range(3):d.observe({})
        self.assertEqual([d.observe({'notice':True}) for _ in range(3)],[[],[],['notice']])
    def test_validation_and_storage(self):
        records,cfg=self.fixture()
        for change in [dict(topic='short'),dict(topic='https://other.example'),dict(watch=['missing']),dict(message='')]:
            with self.assertRaises(ValueError):validate_notifications(dict(cfg,**change),records)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=NotificationStore(folder);store.save(cfg,records);self.assertEqual(store.load(),cfg)
    def test_transport_utf8_timeout_and_no_screenshot(self):
        _,cfg=self.fixture();response=io.BytesIO(b'{}');response.status=200
        opener=Mock(return_value=response);send_notification(cfg,'목표 달성',opener)
        request=opener.call_args.args[0]
        self.assertEqual(request.full_url,'https://ntfy.sh/'+cfg['topic'])
        self.assertEqual(request.data.decode(),'목표 달성');self.assertEqual(opener.call_args.kwargs['timeout'],8)
        with self.assertRaises(ValueError):send_notification(dict(cfg,topic='../bad'),'x',opener)
    def runner(self,dry,stop):
        records,cfg=self.fixture();cfg['stop']=stop
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.dispatch_notification=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
        adb=Mock();adb.screenshot.side_effect=list(range(6))+[RuntimeError('offline')]
        matcher=Mock();matcher.find.return_value=(True,(1,1),1.,(720,1280))
        with patch('app.ADB',return_value=adb),patch('vision.Matcher',return_value=matcher):
            app.run(dict(buttons=records,rules=[],notifications=cfg,dry=dry,adb='adb',serial='device'))
        return app,adb
    def test_real_dispatch_once_and_stop_before_tap(self):
        app,adb=self.runner(False,True)
        app.dispatch_notification.assert_called_once();adb.tap.assert_not_called()
        self.assertEqual(adb.screenshot.call_count,3)
    def test_dry_never_dispatches(self):
        app,adb=self.runner(True,False)
        app.dispatch_notification.assert_not_called();adb.tap.assert_not_called()
        self.assertTrue(any('모의 휴대폰 알림' in c.args[0] for c in app.emit.call_args_list))
    def test_send_failure_is_logged_without_retry(self):
        app=App.__new__(App);app.emit=Mock()
        with patch('app.send_notification',side_effect=RuntimeError('secret')),patch('app.threading.Thread') as thread:
            app.dispatch_notification({},'message');thread.call_args.kwargs['target']()
        self.assertIn('전송 실패',app.emit.call_args.args[0]);self.assertNotIn('secret',app.emit.call_args.args[0])

