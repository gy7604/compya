import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from ranking_summary import parse_record,summary_text,RankingResultReader
from ranking_schedule import DailyRun,KST,defaults,ScheduledRunner
from ranking import RankingMachine


class SummaryTests(unittest.TestCase):
    def test_korean_totals_not_batch_times_five(self):
        self.assertEqual(parse_record('연속 경기 결과\n3 승 1 무 1 패\n확인'),(3,1,1))
        self.assertEqual(parse_record('2승 0무 0패'),(2,0,0))
        self.assertEqual(parse_record('연속 경기 결과 1무 1패 3승'),(3,1,1))
        for text in ('5승','O승 0무 0패','6승0무0패','5승1무0패','0승0무0패','-1승0무0패','3승1무1패\n4승0무1패'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_record(text)

    def test_summary_explicitly_marks_missing_records(self):
        text=summary_text('2026-11-04',dict(completed=2,results={'1':[3,1,1],'2':[2,0,0]}))
        self.assertIn('7경기',text);self.assertIn('5승 1무 1패',text)
        text=summary_text('2026-11-04',dict(completed=3,results={'1':[3,1,1]}))
        self.assertIn('확인된 경기: 5경기',text);self.assertIn('미집계 결과: 2묶음',text)
        self.assertNotIn('당일 총 경기: 15',text)
        unknown=summary_text('2026-11-04',dict(completed=2))
        self.assertIn('판독 확인 필요',unknown);self.assertNotIn('0승 0무 0패',unknown)


class LedgerTests(unittest.TestCase):
    def daily(self,root,dry=False):
        run=DailyRun(root,dict(defaults(),completion_notice=True),lambda:datetime(2026,11,4,14,tzinfo=KST),dry=dry)
        return run

    def notification(self):return dict(enabled=True,topic='test_'+('x'*32))

    def test_batch_record_is_durable_and_duplicate_result_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.start_batch();d.completed(1)
            self.assertTrue(d.result_record(1,(3,1,1)));self.assertFalse(d.result_record(1,(5,0,0)))
            resumed=self.daily(root);resumed.begin()
            self.assertEqual(resumed.entry['results'],{'1':[3,1,1]})
            self.assertIsNone(resumed.entry['active_batch']);self.assertIsNone(resumed.entry['result_pending'])

    def test_uncertain_result_survives_restart_and_can_be_read_again(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.completed(1);d.result_record(1,None)
            resumed=self.daily(root);resumed.begin();self.assertEqual(resumed.entry['result_pending'],1)
            resumed.result_record(1,(4,0,1));self.assertEqual(resumed.entry['results']['1'],[4,0,1])

    def test_send_once_after_finish_and_claim_is_saved_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.completed(1);d.result_record(1,(3,1,1));d.entry['rewards_claimed']=True
            sender=Mock()
            d.notify_pending(self.notification(),sender=sender);sender.assert_not_called()
            d.finish('오늘 허용 횟수 소진')
            def send(config,text):
                saved=self.daily(root).data['days']['2026-11-04']
                self.assertEqual(saved['summary_notice']['state'],'sending')
                self.assertIn('5경기',text);self.assertIn('3승 1무 1패',text);self.assertIn('보상 수령 완료',text)
            sender.side_effect=send
            d.notify_pending(self.notification(),sender=sender)
            self.daily(root).notify_pending(self.notification(),sender=sender)
            sender.assert_called_once();self.assertEqual(d.entry['summary_notice']['state'],'sent')

    def test_queued_done_notice_resumes_but_legacy_done_does_not_send(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.finish('완료')
            sender=Mock();self.daily(root).notify_pending(self.notification(),sender=sender);sender.assert_called_once()
        with tempfile.TemporaryDirectory() as root:
            Path(root,'ranking_daily.json').write_text(json.dumps({'days':{'2026-11-04':{'status':'done','completed':9}}}),encoding='utf-8')
            sender=Mock();self.daily(root).notify_pending(self.notification(),sender=sender);sender.assert_not_called()

    def test_network_failure_or_interrupted_send_never_automatically_resends(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.finish('완료');sender=Mock(side_effect=TimeoutError('private detail'))
            emit=Mock();d.notify_pending(self.notification(),sender=sender,emit=emit)
            self.assertEqual(d.entry['summary_notice']['state'],'unknown')
            self.daily(root).notify_pending(self.notification(),sender=sender);sender.assert_called_once()
            self.assertNotIn('private detail',emit.call_args.args[0])
            d.entry['summary_notice']['state']='sending';d.save()
            self.daily(root).notify_pending(self.notification(),sender=sender);sender.assert_called_once()

    def test_blocked_unclaimed_rewards_and_payment_pending_do_not_notify(self):
        for blocked,field in ((True,None),(False,'reward_pending'),(False,'pending')):
            with self.subTest(blocked=blocked,field=field),tempfile.TemporaryDirectory() as root:
                d=self.daily(root);d.begin()
                if field:d.entry[field]='points' if field=='pending' else True
                d.finish('확인 필요',blocked=blocked);sender=Mock()
                d.notify_pending(self.notification(),sender=sender);sender.assert_not_called()

    def test_dry_and_disabled_master_never_send(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root,dry=True);d.begin();d.finish('완료');sender=Mock()
            d.notify_pending(self.notification(),dry=True,sender=sender);sender.assert_not_called()
            self.assertFalse(Path(root,'ranking_daily.json').exists())
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.finish('완료');sender=Mock()
            d.notify_pending(dict(self.notification(),enabled=False),sender=sender);sender.assert_not_called()

    def test_notification_record_write_failure_never_sends_an_unclaimed_message(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.finish('완료');sender=Mock()
            with patch.object(d,'save',side_effect=PermissionError()):d.notify_pending(self.notification(),sender=sender)
            sender.assert_not_called();self.assertEqual(d.entry['summary_notice']['state'],'pending')

    def test_post_send_save_failure_leaves_durable_sending_claim(self):
        with tempfile.TemporaryDirectory() as root:
            d=self.daily(root);d.begin();d.finish('완료');sender=Mock();original=d.save;calls=[]
            def save():
                calls.append(True)
                if len(calls)==2:raise PermissionError()
                original()
            with patch.object(d,'save',side_effect=save):d.notify_pending(self.notification(),sender=sender)
            self.daily(root).notify_pending(self.notification(),sender=sender)
            sender.assert_called_once()

    def test_supervisor_notifies_after_day_done_then_returns_to_league(self):
        from test_ranking import fixture
        from test_recovery import fixture as recovery_fixture
        records,ranking=fixture();other,recovery=recovery_fixture();records+=other;recovery['target']='league'
        config=dict(buttons=records,ranking=ranking,recovery=recovery,dry=False,adb='adb',serial='device',
                    notifications=self.notification(),schedule=dict(defaults(),enabled=True,completion_notice=True,ranking_steps=['title','menu']))
        app=Mock();app.stop=threading.Event();modes=[]
        def session(cfg):
            modes.append(cfg['ranking']['enabled'])
            if cfg['ranking']['enabled']:
                daily=cfg['_daily'];daily.completed(1);daily.result_record(1,(3,1,1))
                daily.completed(2);daily.result_record(2,(5,0,0));daily.entry['rewards_claimed']=True
                cfg['_ranking_done']='오늘 횟수 소진'
            else:app.stop.set()
        app.run_session.side_effect=session
        with tempfile.TemporaryDirectory() as root,patch('adb.ADB'),patch('ranking_schedule.league_session_visible',return_value=False),\
                patch('notifications.send_notification') as sender:
            runner=ScheduledRunner(app,config,root);runner.daily.clock=lambda:datetime(2026,11,4,14,tzinfo=KST);runner.run()
            self.assertEqual(modes,[True,False]);sender.assert_called_once()
            self.assertIn('10경기',sender.call_args.args[1]);self.assertIn('8승 1무 1패',sender.call_args.args[1])
            self.assertEqual(runner.daily.entry['status'],'done')


class ResultReaderTests(unittest.TestCase):
    def png(self):return cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
    def match(self):
        m=Mock();m.find.return_value=(True,(100,100),1.,(720,1280));return m

    def test_two_frames_two_variants_then_mismatches_and_stop_are_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            reader=RankingResultReader(root);reader.read=Mock(return_value=(4,0,1))
            adb=Mock();adb.screenshot.return_value=self.png();stop=threading.Event()
            self.assertEqual(reader.verify(self.png(),adb,stop,self.match(),self.match()),(4,0,1))
            self.assertEqual(reader.read.call_count,4);adb.tap.assert_not_called()
            reader.read.side_effect=[(4,0,1),(3,1,1),(4,0,1),(4,0,1)]
            self.assertIsNone(reader.verify(self.png(),adb,stop,self.match(),self.match()))
            stop.set();reader.read.reset_mock();self.assertIsNone(reader.verify(self.png(),adb,stop,self.match(),self.match()))
            reader.read.assert_not_called()

    def test_result_title_and_confirmation_must_still_be_present(self):
        with tempfile.TemporaryDirectory() as root:
            reader=RankingResultReader(root);reader.read=Mock();missing=self.match();missing.find.return_value=(False,(0,0),0.,(720,1280))
            self.assertIsNone(reader.verify(self.png(),Mock(),threading.Event(),missing,self.match()))
            reader.read.assert_not_called()

    def test_local_reader_requests_korean_without_changing_currency_default(self):
        with tempfile.TemporaryDirectory() as root:
            helper=Path(root,'PriceOCR.exe');helper.touch()
            reader=RankingResultReader(root)
            with patch('ranking_summary.subprocess.run',return_value=Mock(returncode=0,stdout='4승 0무 1패'.encode())) as run:
                self.assertEqual(reader.read(np.zeros((50,200,3),np.uint8),root,0),(4,0,1))
                self.assertEqual(run.call_args.args[0][-1],'ko')

    def test_screen_change_or_disconnect_after_ocr_vetoes_old_confirm_click(self):
        with tempfile.TemporaryDirectory() as root:
            reader=RankingResultReader(root);adb=Mock();adb.screenshot.return_value=self.png()
            title,confirm=self.match(),self.match();stop=threading.Event()
            self.assertTrue(reader.confirm_visible(adb,stop,title,confirm))
            confirm.find.return_value=(False,(0,0),0.,(720,1280))
            self.assertFalse(reader.confirm_visible(adb,stop,title,confirm))
            adb.screenshot.side_effect=RuntimeError('offline')
            self.assertFalse(reader.confirm_visible(adb,stop,title,title));adb.tap.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def test_only_persisted_active_batch_can_restore_initial_result_count(self):
        from test_ranking import fixture,visible
        for pending,expected in ((None,2),(3,3)):
            records,cfg=fixture();cfg.update(all_attempts=True,resume_active_batch=pending)
            m=RankingMachine(cfg,records,1);m.completed=2
            for t in range(3):m.observe(visible('result','confirm'),t)
            self.assertEqual(m.completed,expected)
            for t in range(3,10):m.observe(visible('result','confirm'),t)
            self.assertEqual(m.completed,expected)

    def test_runner_records_pending_result_before_confirmation_and_skips_duplicate(self):
        from app import App
        from test_ranking import fixture
        with tempfile.TemporaryDirectory() as root:
            d=DailyRun(root,dict(defaults(),completion_notice=True),lambda:datetime(2026,11,4,14,tzinfo=KST));d.begin();d.start_batch()
            for index in range(2):
                app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
                app.emit=Mock();app.events=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
                records,ranking=fixture();adb=Mock();adb.screenshot.side_effect=list(range(7))+[RuntimeError('end replay')]
                def factory(path,*args,**kwargs):
                    m=Mock();m.find.return_value=(str(path) in ('result.png','confirm.png'),(100,200),.99,(720,1280));return m
                reader=Mock();reader.verify.return_value=(3,1,1)
                adb.tap.side_effect=lambda point:self.assertEqual(d.entry['results']['1'],[3,1,1])
                cfg=dict(buttons=records,ranking=ranking,adb='adb',serial='device',dry=False,_daily=d,schedule=d.config)
                with patch('app.ROOT',Path(root)),patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=factory),\
                        patch('ranking_summary.RankingResultReader',return_value=reader),patch('diagnostics.write_stop_snapshot'):
                    app.run_session(cfg)
                self.assertEqual(d.entry['completed'],1)
                self.assertEqual(reader.verify.call_count,1 if index==0 else 0)
                self.assertTrue(adb.tap.called)


if __name__=='__main__':unittest.main()
