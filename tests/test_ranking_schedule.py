import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
import threading
from ranking_schedule import (KST, DailyRun, ScheduledRunner, defaults, policy,
                              guard_action, schedule_lock)
from engine import SafetyStop
from ranking import RankingMachine
from test_ranking import fixture, visible


class ScheduleTests(unittest.TestCase):
    def test_scheduled_all_attempts_does_not_stop_at_manual_target(self):
        records,cfg=fixture();cfg['all_attempts']=True
        machine=RankingMachine(cfg,records,1)
        machine.phase='playing'
        for i in range(3):machine.observe(visible('result'),i)
        self.assertEqual(machine.completed,1)
        self.assertFalse(machine.finished)
        self.assertFalse(machine.target_reached)
        self.assertTrue(any('오늘 완료 1회' in message for message in machine.events))

    def test_supervisor_handoffs_after_session_yield_then_returns_to_league(self):
        from test_recovery import fixture as recovery_fixture
        records, ranking=fixture()
        other,recovery=recovery_fixture();records+=other
        recovery['target']='league'
        cfg=dict(buttons=records,ranking=ranking,recovery=recovery,dry=False,adb='adb',serial='device',
                 schedule=dict(defaults(),enabled=True,ranking_steps=['title','menu']))
        app=Mock();app.stop=threading.Event()
        clock=[datetime(2026,11,2,12,tzinfo=KST)]
        sessions=[]
        def session(config):
            sessions.append(config)
            if len(sessions)==1:
                self.assertFalse(config['_handoff']())
                clock[0]=datetime(2026,11,2,13,tzinfo=KST)
                self.assertTrue(config['_handoff']());config['_yielded']=True
            elif len(sessions)==2:
                config['_daily'].completed(4);config['_ranking_done']='허용 횟수 소진'
            else:app.stop.set()
        app.run_session.side_effect=session
        with tempfile.TemporaryDirectory() as root,patch('adb.ADB') as adb:
            runner=ScheduledRunner(app,cfg,root);runner.daily.clock=lambda:clock[0]
            runner.run()
            self.assertEqual([c['ranking']['enabled'] for c in sessions],[False,True,False])
            self.assertEqual(sessions[1]['recovery']['target'],'ranking')
            self.assertFalse(sessions[1]['ranking']['allow_star'])
            self.assertEqual(sessions[2]['recovery']['target'],'league')
            self.assertFalse(runner.daily.due())
            self.assertEqual(adb.return_value.stop_app.call_count,3)

    def test_korean_time_not_host_time(self):
        self.assertEqual(policy(datetime(2026,11,2,4,tzinfo=timezone.utc)),('2026-11-02',True,False))
        self.assertEqual(policy(datetime(2026,11,2,3,59,tzinfo=timezone.utc)),('2026-11-02',False,False))
        self.assertEqual(policy(datetime(2026,11,7,10,59,tzinfo=timezone.utc)),('2026-11-07',False,True))
        self.assertEqual(policy(datetime(2026,11,7,11,tzinfo=timezone.utc)),('2026-11-07',True,True))
        with self.assertRaises(ValueError):policy(datetime(2026,11,2,13))

    def test_late_start_daily_done_and_next_day(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,2,22,tzinfo=KST)]
            run=DailyRun(root,defaults(),lambda:now[0]);self.assertTrue(run.due())
            run.begin();run.completed(4);run.finish('소진')
            resumed=DailyRun(root,defaults(),lambda:now[0]);self.assertFalse(resumed.due())
            self.assertEqual(resumed.data['days']['2026-11-02']['completed'],4)
            now[0]=datetime(2026,11,3,12,tzinfo=KST);self.assertFalse(resumed.due())
            now[0]=datetime(2026,11,3,13,tzinfo=KST);self.assertTrue(resumed.due())

    def test_pending_payment_survives_crash_and_blocks_double_purchase(self):
        with tempfile.TemporaryDirectory() as root:
            clock=lambda:datetime(2026,11,2,13,tzinfo=KST)
            run=DailyRun(root,defaults(),clock);run.begin();run.reserve('points')
            resumed=DailyRun(root,defaults(),clock);resumed.begin()
            with self.assertRaises(SafetyStop):resumed.reserve('points')
            self.assertEqual(resumed.entry['purchases']['points'],1)
            resumed.verified();resumed.reserve('free')
            self.assertEqual(resumed.entry['purchases']['free'],1)

    def test_weekday_star_veto_and_date_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,2,13,tzinfo=KST)]
            run=DailyRun(root,defaults(),lambda:now[0]);run.begin()
            with self.assertRaises(SafetyStop):run.reserve('star')
            now[0]=datetime(2026,11,2,12,tzinfo=KST)
            with self.assertRaises(SafetyStop):run.reserve('free')
            now[0]=datetime(2026,11,3,13,tzinfo=KST)
            with self.assertRaises(SafetyStop):run.reserve('points')

    def test_weekend_star_cap_and_dry_state_is_separate(self):
        with tempfile.TemporaryDirectory() as root:
            cfg=dict(defaults(),star_limit=1)
            clock=lambda:datetime(2026,11,7,20,tzinfo=KST)
            run=DailyRun(root,cfg,clock);run.begin();run.reserve('star');run.verified()
            with self.assertRaises(SafetyStop):run.reserve('star')
            dry=DailyRun(root,cfg,clock,dry=True);dry.begin();dry.reserve('star')
            self.assertNotEqual(run.path,dry.path)

    def test_exclusive_profile_lock(self):
        with tempfile.TemporaryDirectory() as root:
            with schedule_lock(root):
                with self.assertRaises(SafetyStop):
                    with schedule_lock(root):pass
            with schedule_lock(root):pass

    def test_price_match_rejects_one_changed_digit_in_otherwise_identical_button(self):
        import cv2
        import numpy as np
        from vision import PriceMatcher
        def button(text):
            image=np.full((60,230,3),(180,70,10),np.uint8)
            cv2.putText(image,text,(25,40),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2)
            return image
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'price.png'
            cv2.imencode('.png',button('30000'))[1].tofile(path)
            matcher=PriceMatcher(path,.94)
            self.assertTrue(matcher.find(cv2.imencode('.png',button('30000'))[1].tobytes())[0])
            self.assertFalse(matcher.find(cv2.imencode('.png',button('80000'))[1].tobytes())[0])

    def test_payment_requires_price_and_persists_before_returning_tap(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,10000)
        with tempfile.TemporaryDirectory() as root:
            run=DailyRun(root,defaults(),lambda:datetime(2026,11,2,13,tzinfo=KST));run.begin()
            with self.assertRaises(SafetyStop):guard_action(run,machine,'points_confirm',{},defaults())
            self.assertIsNone(run.entry['pending'])
            conf=dict(defaults(),points_price='price')
            run.price_frames={'price':3}
            self.assertEqual(guard_action(run,machine,'points_confirm',{'price':(True,)},conf),'points_confirm')
            self.assertEqual(run.entry['pending'],'points')
            with self.assertRaises(SafetyStop):guard_action(run,machine,'points_confirm',{'price':(True,)},conf)

    def test_verified_series_clears_intent_even_after_machine_resets_debounce(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,10000)
        machine.phase='series'
        with tempfile.TemporaryDirectory() as root:
            run=DailyRun(root,defaults(),lambda:datetime(2026,11,2,13,tzinfo=KST));run.begin();run.reserve('free')
            seen=visible('lobby','series')
            for i in range(3):
                action=machine.observe(seen,i)
                guard_action(run,machine,action,{k:(v,) for k,v in seen.items()},defaults())
            self.assertIsNone(run.entry['pending'])
            self.assertEqual(machine.phase,'lineup')

    def test_weekday_only_star_remaining_completes_without_buying(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,10000)
        with tempfile.TemporaryDirectory() as root:
            run=DailyRun(root,defaults(),lambda:datetime(2026,11,2,13,tzinfo=KST));run.begin()
            seen=visible('lobby','disabled','paid')
            for i in range(3):
                action=machine.observe(seen,i)
                guard_action(run,machine,action,{k:(v,) for k,v in seen.items()},defaults())
            self.assertTrue(machine.finished)
            self.assertIsNone(run.entry['pending'])


if __name__=='__main__':unittest.main()
