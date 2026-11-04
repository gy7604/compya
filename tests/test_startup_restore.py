import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from startup_restore import ResumeStore,set_startup,RUN_NAME,RUN_KEY,startup_command
from pc_restart import restart_pc,RebootTimer
from app import App


class RestoreTests(unittest.TestCase):
    def test_running_intent_and_mode_survive_restart_but_manual_stop_is_durable(self):
        with tempfile.TemporaryDirectory() as root:
            store=ResumeStore(root);store.update(enabled=True,confirmed=True,dry=False,serial='127.0.0.1:5555',desired_running=True)
            restored=ResumeStore(root);self.assertTrue(restored.should_resume('127.0.0.1:5555'));self.assertFalse(restored.data['dry'])
            self.assertFalse(restored.should_resume('emulator-5580'))
            app=App.__new__(App);app.resume_store=restored;app.stop=threading.Event();app.request_stop()
            self.assertTrue(app.stop.is_set());self.assertFalse(ResumeStore(root).should_resume('127.0.0.1:5555'))

    def test_missing_or_unconfirmed_settings_never_start_game(self):
        with tempfile.TemporaryDirectory() as root:
            store=ResumeStore(root);store.update(enabled=True,desired_running=True,confirmed=False,serial='127.0.0.1:5555')
            self.assertFalse(store.should_resume('127.0.0.1:5555'))
            app=App.__new__(App);app.resume_store=store;app.closing=False;app.fields={'serial':Mock()};app.fields['serial'].get.return_value='127.0.0.1:5555';app.start=Mock();app.emit=Mock()
            app.restore_session();app.start.assert_not_called()
            store.update(confirmed=True);app.start.return_value=(True,'started');app.restore_session();app.start.assert_called_once_with(remote=True)

    def test_logon_registration_is_one_current_user_entry_with_no_shell_or_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            exe=Path(root)/'V26Macro.exe';exe.write_bytes(b'MZ');registry=Mock();registry.HKEY_CURRENT_USER=1;registry.KEY_SET_VALUE=2;registry.REG_SZ=3
            registry.CreateKeyEx.return_value.__enter__=Mock(return_value='key');registry.CreateKeyEx.return_value.__exit__=Mock(return_value=False)
            set_startup(exe,True,registry);registry.CreateKeyEx.assert_called_with(1,RUN_KEY,0,2)
            registry.SetValueEx.assert_called_once_with('key',RUN_NAME,0,3,startup_command(exe))
            self.assertTrue(startup_command(exe).endswith('" --restore-session'))
            set_startup(exe,False,registry);registry.DeleteValue.assert_called_once_with('key',RUN_NAME)

    def test_reboot_waits_twenty_seconds_and_cancel_sends_no_windows_command(self):
        done=Mock();action=Mock();timer=RebootTimer(done,action);timer.cancelled=Mock();timer.cancelled.wait.return_value=True
        timer.run();timer.cancelled.wait.assert_called_once_with(20);action.assert_not_called();done.assert_not_called()
        timer=RebootTimer(done,action);self.assertTrue(timer.cancel());timer.run();action.assert_not_called()

    def test_expired_reboot_timer_sends_one_nonforcing_request(self):
        runner=Mock();runner.return_value.returncode=0;restart_pc(runner)
        args=runner.call_args.args[0];self.assertNotIn('/f',args);self.assertEqual(args[args.index('/t')+1],'0');self.assertEqual(runner.call_count,1)
        action=Mock();done=Mock();timer=RebootTimer(done,action);timer.cancelled=Mock();timer.cancelled.wait.return_value=False;timer.cancelled.is_set.return_value=False
        timer.run();action.assert_called_once_with();done.assert_called_once_with(True);self.assertFalse(timer.cancel())

    def test_reboot_preserves_running_intent_and_cancel_keeps_macro_stopped(self):
        with tempfile.TemporaryDirectory() as root,patch('pc_restart.RebootTimer') as factory:
            app=App.__new__(App);app.resume_store=ResumeStore(root);app.resume_store.update(enabled=True)
            app.confirm=Mock();app.confirm.get.return_value=True;app.dry=Mock();app.dry.get.return_value=False;app.fields={'serial':Mock()};app.fields['serial'].get.return_value='127.0.0.1:5555'
            app.worker=Mock();app.worker.is_alive.return_value=True;app.stop=threading.Event();app.events=Mock();app.emit=Mock();app.reboot_pending=False
            self.assertTrue(app.mobile_reboot()[0]);self.assertTrue(app.resume_store.should_resume('127.0.0.1:5555'));factory.return_value.start.assert_called_once()
            factory.return_value.cancel.return_value=True
            self.assertTrue(app.cancel_mobile_reboot()[0]);self.assertFalse(app.resume_store.data['desired_running']);self.assertFalse(app.reboot_pending)
