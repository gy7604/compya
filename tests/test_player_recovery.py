import threading
import unittest
from unittest.mock import Mock,patch

from app import App
from engine import SafetyStop
from recovery import RecoveryMachine
from test_recovery import fixture


class PlayerRecoveryTests(unittest.TestCase):
    def test_restart_resets_stale_route_without_erasing_shared_budget(self):
        records,cfg=fixture();cfg['max_launches']=2
        m=RecoveryMachine(cfg,records)
        m.active=True;m.route_since=0;m.pending='title';m.suspended=('menu','step')
        self.assertTrue(m.reserve_player_restart(700))
        self.assertEqual(m.route_since,700)
        self.assertIsNone(m.pending);self.assertIsNone(m.suspended)
        self.assertEqual(m.launches,1)
        self.assertFalse(m.reserve_player_restart(710))
        self.assertEqual(m.launches,1)
        self.assertTrue(m.reserve_player_restart(740))
        with self.assertRaises(SafetyStop):m.reserve_player_restart(800)
        self.assertEqual(m.launches,2)

    def run_case(self,fail_connect=False,dry=False,fail_screenshot=False):
        records,cfg=fixture()
        cfg.update(target='league',stall_enabled=True,restart_player=True)
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda x:x
        adb=Mock();adb.app_running.return_value=True;adb.screenshot.return_value=b'frame'
        if fail_connect:adb.connect.side_effect=RuntimeError('offline')
        if fail_screenshot:adb.screenshot.side_effect=RuntimeError('capture timeout')
        player=Mock();player.index=0
        player.restart.return_value=True
        adb.launch_app.side_effect=lambda package:app.stop.set()
        watchdog=Mock();watchdog.seconds=60
        watchdog.observe.side_effect=[False,True]
        def matcher(path,*args,**kwargs):
            value=Mock();value.find.return_value=(path=='title.png',(100,200),.99,(720,1280));return value
        config=dict(buttons=records,recovery=cfg,adb='adb',serial='emulator-5554',dry=dry)
        with patch('app.ADB',return_value=adb),patch('player_restart.PlayerRestart',return_value=player), \
             patch('app.StallWatch',return_value=watchdog),patch('vision.Matcher',side_effect=matcher), \
             patch('app.time.monotonic',side_effect=range(1000)),patch('diagnostics.write_stop_snapshot'):
            app.run(config)
        return app,adb,player

    def test_frozen_screen_is_watched_while_recovery_is_active(self):
        app,adb,player=self.run_case()
        player.restart.assert_called_once()
        adb.launch_app.assert_called_once()
        adb.tap.assert_not_called()
        self.assertTrue(any('복귀 중 화면 정지' in c.args[0] for c in app.emit.call_args_list))

    def test_three_initial_adb_failures_restart_before_any_tap(self):
        _,adb,player=self.run_case(fail_connect=True)
        self.assertEqual(adb.connect.call_count,3)
        player.restart.assert_called_once();adb.tap.assert_not_called()

    def test_dry_adb_failure_never_restarts_or_launches(self):
        _,adb,player=self.run_case(fail_connect=True,dry=True)
        player.restart.assert_not_called();adb.launch_app.assert_not_called();adb.tap.assert_not_called()

    def test_three_capture_failures_restart_even_with_connected_device(self):
        _,adb,player=self.run_case(fail_screenshot=True)
        self.assertEqual(adb.screenshot.call_count,3)
        player.restart.assert_called_once();adb.tap.assert_not_called()
