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
    def reward_fixture(self):
        from registry import new_button
        records,cfg=fixture()
        for key in ('reward','reward_confirm'):
            record=new_button(key);record.update(id=key,enabled=True,kind='condition',template=key+'.png',roi=[0,0,720,1280],click_mode='fixed',click_point=[600,1100],click_size=[720,1280]);records.append(record);cfg[key]=key
        return records,cfg

    def test_weekday_exhaustion_waits_for_glove_and_records_receipt(self):
        records,cfg=self.reward_fixture();machine=RankingMachine(cfg,records,100)
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,defaults(),lambda:datetime(2026,11,3,14,tzinfo=KST));daily.begin()
            def observe(keys,now):
                seen=visible(*keys);action=machine.observe(seen,now)
                return guard_action(daily,machine,action,{key:(value,) for key,value in seen.items()},defaults())
            for now in range(3):observe(['lobby','disabled','paid'],now)
            self.assertFalse(machine.finished);self.assertTrue(machine.target_reached)
            for now in range(3,6):action=observe(['lobby','disabled','paid','reward'],now)
            self.assertEqual(action,'reward');self.assertTrue(daily.entry['reward_pending'])
            reloaded=DailyRun(root,defaults(),lambda:datetime(2026,11,3,14,tzinfo=KST));reloaded.begin();self.assertTrue(reloaded.entry['reward_pending'])
            for now in range(6,9):observe(['reward_confirm'],now)
            for now in range(9,12):observe(['lobby'],now)
            self.assertFalse(daily.entry['reward_pending']);self.assertTrue(daily.entry['rewards_claimed'])
            for now in range(12,15):self.assertIsNone(observe(['lobby','disabled','paid'],now))
            self.assertTrue(machine.finished);self.assertIsNone(daily.entry['pending'])
            self.assertEqual(daily.entry['purchases'],dict(free=0,points=0,star=0))

    def test_zero_paid_attempts_does_not_bypass_reward_drain(self):
        records,cfg=self.reward_fixture();machine=RankingMachine(cfg,records,100)
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,defaults(),lambda:datetime(2026,11,3,14,tzinfo=KST));daily.begin();daily.entry['paid_remaining']=0
            for now in range(3):
                seen=visible('lobby','disabled');action=machine.observe(seen,now)
                guard_action(daily,machine,action,{key:(value,) for key,value in seen.items()},defaults())
            self.assertFalse(machine.finished);self.assertTrue(machine.target_reached)

    def test_one_off_star_lobby_does_not_take_weekday_finish_branch(self):
        records,cfg=fixture();cfg['allow_star']=True
        machine=RankingMachine(cfg,records,100)
        config=defaults();config['one_off_star_date']='2026-11-02'
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,config,lambda:datetime(2026,11,2,18,tzinfo=KST));daily.begin()
            seen=visible('lobby','disabled','paid')
            for now in range(3):
                action=machine.observe(seen,now)
                guard_action(daily,machine,action,{k:(v,) for k,v in seen.items()},config)
            self.assertFalse(machine.finished)

    def test_one_off_stars_resume_done_day_once_and_expire(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,2,18,tzinfo=KST)]
            config=defaults();clock=lambda:now[0]
            daily=DailyRun(root,config,clock);daily.begin();daily.completed(10);daily.finish('weekday done')
            config['one_off_star_date']='2026-11-02'
            resumed=DailyRun(root,config,clock)
            self.assertTrue(resumed.due());resumed.begin()
            self.assertEqual(resumed.entry['completed'],10)
            self.assertTrue(resumed.allowed('star'))
            resumed.reserve('star',50,6,12)
            restart=DailyRun(root,config,clock);restart.begin()
            self.assertEqual(restart.entry['pending'],'star')
            with self.assertRaises(SafetyStop):restart.reserve('star',50,6,12)
            restart.verified();restart.finish('all exhausted')
            self.assertFalse(DailyRun(root,config,clock).due())
            now[0]=datetime(2026,11,3,13,tzinfo=KST)
            next_day=DailyRun(root,config,clock);next_day.begin()
            self.assertFalse(next_day.allowed('star'))

    def test_one_off_cannot_reopen_blocked_or_bypass_start_time(self):
        with tempfile.TemporaryDirectory() as root:
            config=defaults();config['one_off_star_date']='2026-11-02'
            daily=DailyRun(root,config,lambda:datetime(2026,11,2,12,tzinfo=KST))
            self.assertFalse(daily.due())
            with self.assertRaises(SafetyStop):daily.begin()
            daily.clock=lambda:datetime(2026,11,2,18,tzinfo=KST)
            daily.begin();daily.finish('unknown',blocked=True)
            self.assertFalse(daily.due())

    def test_observed_final_paid_attempt_finishes_after_result_without_guessing_count(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,100)
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,defaults(),lambda:datetime(2026,11,2,13,tzinfo=KST));daily.begin()
            # Earlier attempts may have been played outside this macro. The
            # game's observed remaining value takes precedence over our count.
            daily.reserve('points',7000,1,12)
            seen=visible('lobby','disabled')
            for now in range(3):
                action=machine.observe(seen,now)
                guard_action(daily,machine,action,{key:(value,) for key,value in seen.items()},defaults())
            self.assertFalse(machine.finished)
            daily.verified()
            guard_action(daily,machine,None,{key:(value,) for key,value in seen.items()},defaults())
            self.assertTrue(machine.finished)
            self.assertEqual(daily.entry['purchases']['points'],1)

    def test_refresh_receipt_survives_debounce_reset_at_phase_change(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,1);machine.phase='refresh_return'
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,defaults(),lambda:datetime(2026,11,2,13,tzinfo=KST));daily.begin();daily.reserve('points')
            seen=visible('lobby','series')
            for now in range(3):
                action=machine.observe(seen,now)
                guard_action(daily,machine,action,{key:(value,) for key,value in seen.items()},defaults())
            self.assertEqual(machine.phase,'series')
            self.assertIsNone(daily.entry['pending'])
            self.assertEqual(daily.entry['purchases']['points'],1)

    def test_completed_batch_confirms_previous_purchase_after_restart(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,1);machine.phase='result_confirm'
        with tempfile.TemporaryDirectory() as root:
            clock=lambda:datetime(2026,11,2,13,tzinfo=KST)
            daily=DailyRun(root,defaults(),clock);daily.begin();daily.completed(5);daily.reserve('points')
            resumed=DailyRun(root,defaults(),clock);resumed.begin()
            machine.completed=5
            guard_action(resumed,machine,None,{},defaults())
            self.assertEqual(resumed.entry['pending'],'points')
            machine.completed=6
            guard_action(resumed,machine,None,{},defaults())
            self.assertIsNone(resumed.entry['pending'])
            self.assertEqual(resumed.entry['purchases']['points'],1)

    def test_popup_absence_without_active_list_is_not_purchase_success(self):
        records,cfg=fixture();machine=RankingMachine(cfg,records,1);machine.phase='refresh_return'
        with tempfile.TemporaryDirectory() as root:
            daily=DailyRun(root,defaults(),lambda:datetime(2026,11,2,13,tzinfo=KST));daily.begin();daily.reserve('points')
            seen=visible('lobby','disabled')
            for now in range(5):
                guard_action(daily,machine,machine.observe(seen,now),{key:(value,) for key,value in seen.items()},defaults())
            self.assertEqual(machine.phase,'refresh_return')
            self.assertEqual(daily.entry['pending'],'points')

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
            self.assertNotIn('_activate_route',sessions[0])
            self.assertEqual(adb.return_value.stop_app.call_count,2)

    def test_first_start_does_not_stop_or_launch_a_healthy_existing_game(self):
        from test_recovery import fixture as recovery_fixture
        records,ranking=fixture();other,recovery=recovery_fixture();records+=other;recovery['target']='league'
        cfg=dict(buttons=records,ranking=ranking,recovery=recovery,dry=False,adb='adb',serial='device',schedule=dict(defaults(),enabled=True,ranking_steps=['title','menu']))
        app=Mock();app.stop=threading.Event();app.run_session.side_effect=lambda config:app.stop.set()
        with tempfile.TemporaryDirectory() as root,patch('adb.ADB') as adb:
            runner=ScheduledRunner(app,cfg,root);runner.daily.clock=lambda:datetime(2026,11,2,12,tzinfo=KST);runner.run()
            adb.assert_not_called()
            self.assertNotIn('_activate_route',app.run_session.call_args.args[0])

    def test_due_start_preserves_current_league_until_result_handoff(self):
        from test_recovery import fixture as recovery_fixture
        records,ranking=fixture();other,recovery=recovery_fixture();records+=other;recovery['target']='league'
        cfg=dict(buttons=records,ranking=ranking,recovery=recovery,dry=False,adb='adb',serial='device',schedule=dict(defaults(),enabled=True,ranking_steps=['title','menu']))
        app=Mock();app.stop=threading.Event();sessions=[]
        def session(config):
            sessions.append(config)
            if len(sessions)==1:
                self.assertFalse(config['ranking']['enabled']);self.assertTrue(config['_handoff']());config['_yielded']=True
            else:app.stop.set()
        app.run_session.side_effect=session
        with tempfile.TemporaryDirectory() as root,patch('adb.ADB') as adb,patch('ranking_schedule.league_session_visible',return_value=True):
            runner=ScheduledRunner(app,cfg,root);runner.daily.clock=lambda:datetime(2026,11,2,13,tzinfo=KST);runner.run()
            self.assertEqual([c['ranking']['enabled'] for c in sessions],[False,True])
            self.assertEqual(adb.return_value.stop_app.call_count,1)

    def test_readonly_probe_requires_two_league_hud_frames(self):
        import cv2,numpy as np,shutil
        from ranking_schedule import league_session_visible
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'assets').mkdir()
            source=Path(__file__).resolve().parents[1]/'assets/league_play_hud.png'
            shutil.copy2(source,root/'assets/league_play_hud.png')
            image=cv2.imread(str(source));frame=np.zeros((1280,720,3),np.uint8);frame[60:120,615:719]=image
            png=cv2.imencode('.png',frame)[1].tobytes();blank=cv2.imencode('.png',np.zeros_like(frame))[1].tobytes()
            cfg=dict(buttons=[],ranking={},recovery={},adb='adb',serial='device')
            with patch('adb.ADB') as adb:
                adb.return_value.screenshot.side_effect=[png,png]
                self.assertTrue(league_session_visible(root,cfg,threading.Event()))
                adb.return_value.stop_app.assert_not_called();adb.return_value.launch_app.assert_not_called()
                adb.return_value.screenshot.side_effect=[png,blank]
                self.assertFalse(league_session_visible(root,cfg,threading.Event()))

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
