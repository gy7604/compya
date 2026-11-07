import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from engine import SafetyStop
from player_restart import PlayerRestart
from recovery import default_recovery,validate_draft


class StartPlayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);exe=root/'ldconsole.exe';exe.touch()
        adb=Mock();adb.serial='127.0.0.1:5555';adb.executable=str(root/'adb.exe');adb.run.return_value=b'1'
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        self.p=PlayerRestart(str(exe),0,adb,stop)

    def test_stopped_zero_launches_once_then_waits_for_boot(self):
        self.p.command=Mock(side_effect=['0,game,0,0,0,-1,-1']*2+[''])
        self.p.adb.run.side_effect=[b'0',b'1']
        self.assertTrue(self.p.ensure_started());self.assertTrue(self.p.started_on_demand)
        self.assertEqual([c.args for c in self.p.command.call_args_list],
                         [('list2',),('list2',),('launch','--index','0')])
        self.assertEqual(self.p.adb.connect.call_count,2)

    def test_healthy_zero_is_never_rebooted_or_launched(self):
        self.p.command=Mock(return_value='0,game,1,2,1,100,200')
        self.assertTrue(self.p.ensure_started());self.assertFalse(self.p.started_on_demand)
        self.p.command.assert_called_once_with('list2');self.p.stop.wait.assert_not_called()

    def test_booting_process_is_waited_and_closing_process_refuses_launch(self):
        self.p.command=Mock(return_value='0,game,1,2,2,100,200')
        self.assertTrue(self.p.ensure_started());self.p.command.assert_called_once_with('list2')
        for row in ('0,game,0,0,0,-1,200','0,game,1,0,0,100,-1'):
            self.p.command=Mock(return_value=row)
            with self.assertRaises(SafetyStop):self.p.ensure_started()
            self.p.command.assert_called_once_with('list2')

    def test_another_launcher_winning_the_race_prevents_second_launch(self):
        self.p.command=Mock(side_effect=['0,game,0,0,0,-1,-1','0,game,1,2,2,100,200'])
        self.assertTrue(self.p.ensure_started());self.assertFalse(self.p.started_on_demand)
        self.assertEqual(self.p.command.call_count,2)

    def test_missing_duplicate_or_invalid_zero_refuses_all_mutations(self):
        for value in ('1,other,1,2,1,100,200','0,game',
                      '0,a,0,0,0,-1,-1\n0,b,0,0,0,-1,-1',
                      '0,game,0,0,garbage,-1,-1','0,game,0,0,8,-1,-1'):
            self.p.command=Mock(return_value=value)
            with self.assertRaises(SafetyStop):self.p.ensure_started()
            self.p.command.assert_called_once_with('list2')
        self.p.adb.connect.assert_not_called()

    def test_nonzero_instance_is_not_started(self):
        self.p.index=1;self.p.command=Mock()
        with self.assertRaises(SafetyStop):self.p.ensure_started()
        self.p.command.assert_not_called()

    def test_stop_before_launch_or_during_boot_sends_no_further_commands(self):
        self.p.command=Mock(return_value='0,game,0,0,0,-1,-1')
        self.p.stop.is_set.return_value=True;self.assertFalse(self.p.ensure_started());self.p.command.assert_not_called()
        self.p.stop.is_set.return_value=False;self.p.stop.wait.return_value=True
        self.assertFalse(self.p.ensure_started());self.p.adb.connect.assert_not_called()

    def test_stop_after_status_check_prevents_launch(self):
        self.p.command=Mock(return_value='0,game,0,0,0,-1,-1')
        self.p.stop.is_set.side_effect=[False,True]
        self.assertFalse(self.p.ensure_started());self.assertEqual(self.p.command.call_count,2)

    def test_boot_timeout_has_no_implicit_reboot_or_global_adb_reset(self):
        self.p.command=Mock(return_value='0,game,1,2,2,100,200')
        self.p.adb.run.return_value=b'0'
        with self.assertRaisesRegex(RuntimeError,'120초'):self.p.ensure_started()
        self.p.command.assert_called_once_with('list2')
        self.assertEqual(self.p.adb.connect.call_count,60)

    def test_start_setting_is_enabled_for_legacy_configs_and_boolean_only(self):
        self.assertTrue(default_recovery()['start_player'])
        for value in ('yes',1,None):
            with self.assertRaises(ValueError):validate_draft(dict(default_recovery(),start_player=value))


class StartSessionTests(unittest.TestCase):
    def run_case(self,game_running=False,dry=False,start=True,restart=True,boot_error=None):
        from app import App
        from test_recovery import fixture
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda value:value
        records,recovery=fixture();recovery.update(target='league',start_player=start,restart_player=restart,max_launches=2)
        adb=Mock();adb.app_running.return_value=game_running
        adb.screenshot.side_effect=lambda:app.stop.set() or 'frame'
        player=Mock();player.index=0;player.started_on_demand=False;player.ensure_started.return_value=True
        if boot_error:player.ensure_started.side_effect=boot_error
        player.restart.side_effect=lambda **kwargs:app.stop.set() or True
        if not game_running:adb.launch_app.side_effect=lambda package:app.stop.set()
        def matcher(*args,**kwargs):
            result=Mock();result.find.return_value=(False,(100,200),.1,(720,1280));return result
        with tempfile.TemporaryDirectory() as folder:
            with patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb), \
                 patch('player_restart.PlayerRestart',return_value=player) as factory,patch('vision.Matcher',side_effect=matcher):
                app.run_session(dict(buttons=records,recovery=recovery,dry=dry,adb='adb',serial='127.0.0.1:5555'))
        return app,adb,player,factory

    def test_cold_start_launches_game_before_any_screenshot_or_tap(self):
        app,adb,player,_=self.run_case()
        player.ensure_started.assert_called_once();adb.launch_app.assert_called_once();adb.screenshot.assert_not_called()
        adb.tap.assert_not_called();adb.stop_app.assert_not_called();player.restart.assert_not_called()
        self.assertTrue(any('접속 광고 처리 및 현재 모드 자동 진입' in c.args[0] for c in app.emit.call_args_list))

    def test_healthy_game_continues_without_launch_or_reboot(self):
        app,adb,player,_=self.run_case(game_running=True)
        player.ensure_started.assert_called_once();adb.launch_app.assert_not_called();adb.stop_app.assert_not_called()
        player.restart.assert_not_called();adb.screenshot.assert_called_once()
        self.assertTrue(any('플레이어/게임 재시작 없음' in c.args[0] for c in app.emit.call_args_list))

    def test_start_is_independent_of_stall_restarts(self):
        _,adb,player,_=self.run_case(restart=False)
        player.ensure_started.assert_called_once();adb.launch_app.assert_called_once();player.restart.assert_not_called()

    def test_disabled_or_dry_start_sends_no_player_or_game_launch(self):
        for options in (dict(start=False),dict(dry=True)):
            _,adb,player,factory=self.run_case(game_running=True,**options)
            factory.assert_not_called();player.ensure_started.assert_not_called();player.restart.assert_not_called()
            adb.launch_app.assert_not_called();adb.stop_app.assert_not_called()

    def test_unknown_state_safely_stops_without_fallback_reboot(self):
        app,adb,player,_=self.run_case(boot_error=SafetyStop('unknown player state'))
        player.restart.assert_not_called();adb.launch_app.assert_not_called();adb.screenshot.assert_not_called()
        self.assertTrue(any('unknown player state' in c.args[0] for c in app.emit.call_args_list))

    def test_boot_failure_uses_existing_bounded_recovery(self):
        _,adb,player,_=self.run_case(boot_error=RuntimeError('boot timeout'))
        player.restart.assert_called_once_with(full=False);adb.tap.assert_not_called()
