import copy
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from engine import SafetyStop
from player_memory import (GIB,MemoryPolicy,MemoryGuard,WindowsMemory,ProcessLease,
                           selected_vm,create_memory_guard,safe_boundary,run_cleanup)
from recovery import RecoveryMachine,default_recovery,validate_draft
from league_count import LeagueCounter
from test_recovery import fixture


def sample(gb=8,commit=35,pid=10):
    return dict(identity=[pid,100],private_bytes=int(gb*GIB),commit_percent=commit)


class MemoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'player_memory_state.json'
        self.p=MemoryPolicy(self.path)

    def test_two_high_samples_require_a_boundary(self):
        self.p.observe(sample(13));self.assertFalse(self.p.pending)
        self.p.observe(sample(13));self.assertTrue(self.p.pending)
        self.assertFalse(self.p.due(False,1000));self.assertTrue(self.p.due(True,1000))

    def test_low_sample_breaks_consecutive_detection(self):
        for gb in (13,8,13):self.p.observe(sample(gb))
        self.assertFalse(self.p.pending)

    def test_new_process_clears_old_high_counts_and_preserves_manual_request(self):
        self.p.observe(sample(13));self.p.observe(sample(13))
        self.p.observe(sample(pid=11));self.assertFalse(self.p.pending)
        self.p.request();self.p.observe(sample(pid=12));self.assertTrue(self.p.pending)

    def test_critical_vm_or_host_commit_does_not_wait_for_result(self):
        for gb,commit in ((16,35),(8,90)):
            with self.subTest(gb=gb):
                p=MemoryPolicy(self.path);p.observe(sample(gb,commit));self.assertTrue(p.due(False,1000))
        p=MemoryPolicy(self.path);p.observe(sample(4,95));self.assertFalse(p.pending)

    def test_custom_soft_limit_adjusts_emergency_limit(self):
        p=MemoryPolicy(self.path,20);p.observe(sample(21));p.observe(sample(21))
        self.assertFalse(p.due(False,1000));p.observe(sample(24));self.assertTrue(p.due(False,1000))

    def test_cooldown_persists_before_side_effect_and_blocks_restart_storm(self):
        self.p.request();self.p.reserve(1000)
        p=MemoryPolicy(self.path);p.request();self.assertFalse(p.due(True,1100))
        self.assertTrue(p.due(True,1600))
        p.observe(sample(18))
        with self.assertRaises(SafetyStop):p.due(False,1100)

    def test_completed_requires_new_process_and_recovered_host(self):
        self.p.observe(sample(16));self.p.reserve(1000)
        for value in (sample(8),sample(13,pid=11),sample(8,95,pid=11)):
            with self.assertRaises(RuntimeError):self.p.completed(value)
        self.p.completed(sample(pid=11));self.assertFalse(self.p.pending)
        self.assertEqual(MemoryPolicy(self.path).last_attempt,1000)

    def test_invalid_numbers_or_configuration_are_rejected(self):
        for limit in (True,7,33,12.5):
            with self.assertRaises(ValueError):MemoryPolicy(self.path,limit)
        for value in (sample(-1),sample(commit=float('nan')),sample(commit=101)):
            with self.assertRaises(ValueError):self.p.observe(value)
        for values in ({'memory_enabled':1},{'memory_limit_gb':True},{'memory_limit_gb':99}):
            with self.assertRaises(ValueError):validate_draft(dict(default_recovery(),**values))
        self.assertTrue(default_recovery()['memory_enabled'])

    def test_poll_is_rate_limited_and_never_restarts_from_a_read(self):
        probe=Mock();probe.read.return_value=sample(13)
        guard=MemoryGuard(probe,self.p)
        for now in (0,1,29,30):guard.poll(now)
        self.assertEqual(probe.read.call_count,2);self.assertTrue(self.p.pending)
        probe.recycle.assert_not_called();self.assertIn('13.0GB',guard.text())
        probe.read.side_effect=RuntimeError('unavailable');guard.poll(60);guard.poll(90)
        self.assertIn('조회 실패',guard.text());self.assertEqual(len(guard.events),2)


class ProcessShutdownTests(unittest.TestCase):
    def test_wrong_executable_or_pid_race_closes_handle_without_quit(self):
        for foreign in (True,False):
            m=WindowsMemory.__new__(WindowsMemory);m.kernel=Mock();m.kernel.OpenProcess.return_value=123
            m.expected=Path('Ld9BoxHeadless.exe').resolve();m.row=Mock(side_effect=[(1,10),(1,11)])
            def image(handle,flags,buffer,length):
                buffer.value=str(Path('foreign.exe').resolve() if foreign else m.expected);return True
            m.kernel.QueryFullProcessImageNameW.side_effect=image
            m.kernel.GetProcessTimes.return_value=True
            with self.assertRaises(RuntimeError):m.open_current()
            m.kernel.CloseHandle.assert_called_once_with(123)

    def make_probe(self,alive,rows):
        m=WindowsMemory.__new__(WindowsMemory);m.player=Mock()
        m.player.stop.is_set.return_value=False;m.player.stop.wait.return_value=False
        m.retiring=None;m.row=Mock(side_effect=rows)
        lease=Mock();lease.pid=10;lease.alive.side_effect=alive
        m.open_current=Mock(return_value=lease)
        return m,lease

    def test_list2_zero_does_not_prove_actual_process_exit(self):
        m,lease=self.make_probe([True,False],[(1,10),(0,-1),(0,-1)])
        self.assertTrue(m.recycle());self.assertEqual(lease.alive.call_count,2)
        lease.close.assert_called_once();m.player.restart.assert_called_once_with()
        m.player.command.assert_called_once_with('quit','--index','0')

    def test_orphan_process_never_leads_to_duplicate_launch_even_on_retry(self):
        m,lease=self.make_probe([True]*60,[(1,10)]+[(0,-1)]*61)
        with self.assertRaises(RuntimeError):m.recycle()
        self.assertIs(m.retiring,lease)
        with self.assertRaises(RuntimeError):m.recycle()
        m.player.restart.assert_not_called();self.assertEqual(m.open_current.call_count,1)
        lease.close.assert_not_called()

    def test_foreign_or_changed_process_refuses_any_restart(self):
        for rows in ([(1,11)],[(1,10),(1,11)]):
            m,_=self.make_probe([False],rows)
            with self.assertRaises(RuntimeError):m.recycle()
            m.player.restart.assert_not_called()

    def test_stop_cancels_quit_or_launch(self):
        m,_=self.make_probe([False],[(1,10),(0,-1)])
        m.player.stop.is_set.return_value=True;self.assertFalse(m.recycle())
        m.player.command.assert_not_called()
        m.player.stop.is_set.return_value=False;m.player.stop.wait.return_value=True
        self.assertFalse(m.recycle());m.player.restart.assert_not_called()

    def test_stopped_instance_can_be_launched_without_quit(self):
        m,_=self.make_probe([],[]);m.open_current.return_value=None
        self.assertTrue(m.recycle());m.player.command.assert_not_called()

    def test_only_exact_zero_row_is_selected(self):
        self.assertEqual(selected_vm('1,Other,0,0,1,20,99\n0,Compya,0,0,1,30,10'),(1,10))
        for value in ('1,Other,0,0,1,20,99','0,broken','0,a,0,0,1,0,10\n0,b,0,0,1,0,20'):
            with self.assertRaises(RuntimeError):selected_vm(value)

    def test_process_handle_is_closed_and_wait_failures_are_explicit(self):
        api=Mock();api.kernel.WaitForSingleObject.side_effect=[258,0,0xffffffff]
        lease=ProcessLease(api,123,10,100)
        self.assertTrue(lease.alive());self.assertFalse(lease.alive())
        with self.assertRaises(RuntimeError):lease.alive()
        lease.close();lease.close();api.kernel.CloseHandle.assert_called_once_with(123)

    def test_dry_disabled_or_other_instance_does_not_create_probe(self):
        for cfg,player,dry in (({},Mock(index=0),True),({'memory_enabled':False},Mock(index=0),False),({},Mock(index=1),False),({},None,False)):
            with patch('player_memory.WindowsMemory') as probe:
                self.assertIsNone(create_memory_guard(cfg,player,'.',dry));probe.assert_not_called()


class CleanupFlowTests(unittest.TestCase):
    def test_success_preserves_shared_fault_budget(self):
        records,cfg=fixture();m=RecoveryMachine(cfg,records);m.launches=2;m.last_launch=100
        guard=Mock();guard.recycle.return_value=True;stop=threading.Event();adb=Mock()
        self.assertTrue(run_cleanup(guard,m,adb,stop,Mock()))
        m.begin_return(300)
        self.assertEqual((m.launches,m.last_launch),(2,100));self.assertTrue(m.active)
        adb.launch_app.assert_called_once_with(cfg['package'])

    def test_failed_boots_are_bounded_and_use_existing_failure_alert_path(self):
        records,cfg=fixture();cfg['max_launches']=10;m=RecoveryMachine(cfg,records)
        guard=Mock();guard.recycle.side_effect=RuntimeError('old process alive')
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False;adb=Mock()
        with self.assertRaises(SafetyStop):run_cleanup(guard,m,adb,stop,Mock())
        self.assertEqual(guard.recycle.call_count,10);self.assertEqual(m.launches,10);self.assertTrue(m.active)
        adb.launch_app.assert_not_called()

    def test_stop_during_cleanup_does_not_launch_game(self):
        records,cfg=fixture();m=RecoveryMachine(cfg,records);stop=threading.Event();guard=Mock()
        guard.recycle.side_effect=lambda:stop.set() or False;adb=Mock()
        self.assertFalse(run_cleanup(guard,m,adb,stop,Mock()));adb.launch_app.assert_not_called()

    def test_league_count_is_not_incremented_again_after_maintenance(self):
        counter=LeagueCounter('play','result',2)
        for key in ('play','play','result','result'):counter.observe({key:True})
        self.assertTrue(safe_boundary(True,counter,None,{}))
        for _ in range(5):self.assertFalse(counter.observe({'result':True}))
        self.assertEqual(counter.completed,1)

    def test_ranking_maintenance_preserves_pending_currency_and_records(self):
        rank=SimpleNamespace(config=dict(enabled=True,lobby='lobby'),phase='lobby',counts={'lobby':3})
        entry=dict(completed=12,points=3,star=1,pending=None,active_batch=None,reward_pending=False,result_pending=False)
        day=SimpleNamespace(entry=entry);before=copy.deepcopy(entry)
        self.assertTrue(safe_boundary(False,None,rank,{'lobby':(True,)},day))
        self.assertEqual(entry,before)
        for key in ('pending','active_batch','reward_pending','result_pending'):
            entry[key]='in progress'
            self.assertFalse(safe_boundary(False,None,rank,{'lobby':(True,)},day));entry[key]=before[key]
        for phase in ('playing','star_confirm','reward_return','departing'):
            rank.phase=phase;self.assertFalse(safe_boundary(False,None,rank,{'lobby':(True,)},day))


class MemoryAppTests(unittest.TestCase):
    def run_case(self,urgent=False,failure=False,dry=False,handoff=False):
        from app import App
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.events=queue.Queue();app.emit=Mock();app.dispatch_notification=Mock()
        app.registry=Mock();app.registry.resolve.side_effect=lambda value:value
        records,cfg=fixture();cfg.update(target='league',memory_enabled=True,max_launches=10)
        result=copy.deepcopy(records[0]);result.update(id='result',name='result',template='result.png');records.append(result)
        adb=Mock();adb.app_running.return_value=True
        screenshots=['goal','goal','result','result']
        def screen():
            if not screenshots:raise AssertionError('cleanup did not run at the result')
            return screenshots.pop(0)
        adb.screenshot.side_effect=screen
        adb.launch_app.side_effect=lambda package:app.stop.set()
        def matcher(path,*args,**kwargs):
            result=Mock();result.find.side_effect=lambda png:(path==png+'.png',(100,200),.99,(720,1280));return result
        player=Mock();player.index=0
        with tempfile.TemporaryDirectory() as folder:
            policy=MemoryPolicy(Path(folder)/'player_memory_state.json')
            policy.observe(sample(16 if urgent else 13));policy.observe(sample(16 if urgent else 13))
            probe=Mock();probe.read.side_effect=[sample(16 if urgent else 13),sample(pid=11)]
            def recycle():
                self.assertTrue(policy.path.exists())
                if failure:raise RuntimeError('cannot boot')
                return True
            probe.recycle.side_effect=recycle;guard=MemoryGuard(probe,policy)
            options=dict(buttons=records,recovery=cfg,dry=dry,adb='adb',serial='emulator-5554',
                         league_start='goal',league_result='result',speed='균형 · 2회 확인')
            if handoff:options['_handoff']=lambda:True
            if failure:options['notifications']=dict(enabled=True,watch=['title'],topic='test_'+('a'*32),message='test',stop=False)
            with patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher), \
                 patch('player_restart.PlayerRestart',return_value=player),patch('app.create_memory_guard',return_value=None if dry else guard), \
                 patch('diagnostics.write_stop_snapshot'):
                if dry:adb.screenshot.side_effect=lambda:app.stop.set() or 'none'
                app.run_session(options)
        return app,adb,probe

    def test_urgent_cleanup_happens_before_screenshot_or_opencv(self):
        app,adb,probe=self.run_case(urgent=True)
        adb.screenshot.assert_not_called();probe.recycle.assert_called_once();adb.launch_app.assert_called_once()
        self.assertFalse(any('안전 중지' in c.args[0] for c in app.emit.call_args_list))

    def test_soft_cleanup_waits_for_stable_result_and_counts_before_reboot(self):
        app,adb,probe=self.run_case()
        self.assertEqual(adb.screenshot.call_count,4);probe.recycle.assert_called_once()
        messages=[c.args[0] for c in app.emit.call_args_list]
        self.assertTrue(any('리그 경기 완료 · 이번 실행 1경기' in msg for msg in messages))
        self.assertFalse(any('안전 중지' in msg for msg in messages))

    def test_exhausted_memory_cleanup_stops_without_game_taps(self):
        app,adb,probe=self.run_case(urgent=True,failure=True)
        self.assertEqual(probe.recycle.call_count,10);adb.screenshot.assert_not_called();adb.tap.assert_not_called()
        app.dispatch_notification.assert_called_once()
        self.assertTrue(any('메모리 정리 재접속 한도 도달' in c.args[0] for c in app.emit.call_args_list))

    def test_scheduled_ranking_handoff_precedes_nonurgent_league_cleanup(self):
        app,adb,probe=self.run_case(handoff=True)
        self.assertEqual(adb.screenshot.call_count,4);probe.recycle.assert_not_called()
        self.assertTrue(any('다음 경기 시작 전 랭킹으로 전환' in c.args[0] for c in app.emit.call_args_list))

    def test_dry_session_does_not_call_memory_recycle(self):
        _,adb,probe=self.run_case(dry=True)
        probe.recycle.assert_not_called();adb.launch_app.assert_not_called();adb.tap.assert_not_called()
