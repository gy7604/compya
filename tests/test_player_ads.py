import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from player_ads import NativeSurface,PlayerAds,PlayerWindowClosed,is_player_ad,selected_row
from engine import SafetyStop


def snapshot(ads=(20,),minimized=False):return dict(handle=10,pid=100,ads=ads,minimized=minimized)


class PlayerAdTests(unittest.TestCase):
    def test_host_ad_prevents_all_adb_input_in_league_and_ranking(self):
        from app import App
        from test_recovery import fixture as league_fixture
        from test_ranking import fixture as ranking_fixture
        records,recovery=league_fixture();recovery['target']='league'
        ranked,ranking=ranking_fixture()
        for options in [dict(buttons=records,recovery=recovery),dict(buttons=ranked,ranking=ranking)]:
            with self.subTest(ranking=bool(options.get('ranking'))),tempfile.TemporaryDirectory() as folder:
                app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False);app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda value:value
                adb=Mock();guard=Mock(resumed=False,events=[])
                def hold(*args):
                    if guard.observe.call_count>=3:app.stop.set()
                    return True
                guard.observe.side_effect=hold
                with patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),patch('vision.Matcher'),patch('player_ads.create_player_ads',return_value=guard):
                    app.run_session(dict(options,dry=False,adb='adb',serial='device'))
                self.assertEqual(guard.observe.call_count,3)
                adb.connect.assert_not_called();adb.screenshot.assert_not_called();adb.tap.assert_not_called();adb.stop_app.assert_not_called();adb.launch_app.assert_not_called()

    def test_only_instance_zero_row_is_used_and_duplicates_are_rejected(self):
        self.assertEqual(selected_row('1,other,30,40,1,200,300\n0,ours,10,11,1,100,101'),(10,100))
        for data in ['1,other,30,40,1,200,300','0,ours,0,0,0,0,0','0,bad,no,1,1,2,3','0,ours,10,11,1,100,101\n0,again,20,21,1,200,201']:
            with self.assertRaises(RuntimeError):selected_row(data)

    def test_other_apps_players_dialogs_main_window_and_outside_rect_never_qualify(self):
        root=dict(handle=10,rect=(100,100,860,1415));ad=dict(handle=20,kind='ADFullScreenFrame',pid=100,owner=10,visible=True,rect=(102,134,822,1414))
        self.assertTrue(is_player_ad(ad,root,100))
        for values in [dict(handle=10),dict(kind='LDPlayerMainFrame'),dict(kind='#32770'),dict(pid=200),dict(owner=30),dict(visible=False),dict(rect=(0,0,720,1280))]:
            self.assertFalse(is_player_ad(dict(ad,**values),root,100))

    def test_first_detection_holds_close_after_three_polls_and_resume_after_three_clear(self):
        surface=Mock();surface.capture.return_value=snapshot();surface.close.return_value=True
        guard=PlayerAds(surface,threading.Event())
        self.assertTrue(guard.observe(0));self.assertTrue(guard.observe(1));surface.close.assert_not_called()
        self.assertTrue(guard.observe(2));surface.close.assert_called_once()
        surface.capture.return_value=snapshot(())
        self.assertTrue(guard.observe(3));self.assertTrue(guard.observe(4));self.assertFalse(guard.observe(5));self.assertTrue(guard.resumed)

    def test_manual_close_before_debounce_also_requires_three_absent_polls(self):
        surface=Mock();surface.capture.return_value=snapshot();guard=PlayerAds(surface,threading.Event());guard.observe(0)
        surface.capture.return_value=snapshot(())
        self.assertTrue(guard.observe(1));self.assertTrue(guard.observe(2));self.assertFalse(guard.observe(3));surface.close.assert_not_called()

    def test_capture_error_or_minimized_window_cannot_release_known_ad_hold(self):
        surface=Mock();surface.capture.return_value=snapshot();guard=PlayerAds(surface,threading.Event());guard.observe(0)
        surface.capture.side_effect=RuntimeError('unavailable')
        for now in (1,2):self.assertTrue(guard.observe(now))
        with self.assertRaises(SafetyStop):guard.observe(3)
        surface.capture.side_effect=None;surface.capture.return_value=snapshot((),True)
        for now in (4,5,6):self.assertTrue(guard.observe(now))
        self.assertFalse(guard.resumed);surface.close.assert_not_called()

    def test_confirmed_player_shutdown_yields_to_existing_recovery(self):
        surface=Mock();surface.capture.return_value=snapshot();guard=PlayerAds(surface,threading.Event());guard.observe(0)
        surface.capture.side_effect=PlayerWindowClosed('stopped')
        self.assertFalse(guard.observe(1));self.assertTrue(guard.resumed);surface.close.assert_not_called()

    def test_dry_run_and_stop_never_close_and_persistent_ads_have_a_retry_limit(self):
        for dry,stopped in [(True,False),(False,True)]:
            surface=Mock();surface.capture.return_value=snapshot();stop=threading.Event()
            if stopped:stop.set()
            guard=PlayerAds(surface,stop)
            for now in range(3):guard.observe(now,dry)
            surface.close.assert_not_called()
        surface=Mock();surface.capture.return_value=snapshot();guard=PlayerAds(surface,threading.Event())
        for now in range(26):guard.observe(now)
        with self.assertRaises(SafetyStop):guard.observe(26)
        self.assertEqual(surface.close.call_count,3)

    def test_native_close_rechecks_owner_instance_stale_and_multiple_handles(self):
        surface=NativeSurface.__new__(NativeSurface);surface.user=Mock();surface.verify=Mock(return_value=True)
        surface.capture=Mock(return_value=snapshot())
        self.assertTrue(surface.close(snapshot()));surface.user.PostMessageW.assert_called_once_with(20,0x10,0,0)
        for current in [dict(snapshot(),handle=30),dict(snapshot(),pid=200),snapshot((21,)),snapshot((20,21)),snapshot((10,)),snapshot(())]:
            surface.user.reset_mock();surface.capture.return_value=current
            self.assertFalse(surface.close(snapshot()));surface.user.PostMessageW.assert_not_called()


if __name__=='__main__':unittest.main()
