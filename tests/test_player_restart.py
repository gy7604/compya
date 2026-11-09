import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from player_restart import PlayerRestart,validate_serial

class PlayerTests(unittest.TestCase):
    def test_rejects_other_instance_serial(self):
        validate_serial(0,'emulator-5554')
        validate_serial(2,'127.0.0.1:5559')
        with self.assertRaises(ValueError):validate_serial(0,'emulator-5556')
    def build(self,path):
        adb=Mock();adb.serial='emulator-5554';adb.executable=str(path/'adb.exe')
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        exe=path/'ldconsole.exe';exe.touch()
        return PlayerRestart(str(exe),0,adb,stop)
    def test_only_selected_instance_reboot_then_wait_for_boot(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(side_effect=['0,game,1,2,1,99,88',''])
            p.adb.run.side_effect=[b'0',b'1']
            self.assertTrue(p.restart())
            self.assertEqual(p.command.call_args_list[1].args,('reboot','--index','0'))
            self.assertEqual(p.adb.connect.call_count,2)
    def test_missing_instance_never_reboots(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(return_value='1,other,1,2,1,99,88')
            with self.assertRaises(RuntimeError):p.restart()
            p.command.assert_called_once_with('list2')
    def test_reconnect_uses_only_same_instance_loopback_not_device_list(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.adb.connect.side_effect=[RuntimeError('missing serial'),None]
            p.connect_selected()
            self.assertEqual(p.adb.serial,'127.0.0.1:5555')
            self.assertEqual(p.adb.connect.call_count,2)
            p.adb.run.assert_not_called()
    def test_failed_fallback_restores_original_serial(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.adb.connect.side_effect=RuntimeError('offline')
            with self.assertRaises(RuntimeError):p.connect_selected()
            self.assertEqual(p.adb.serial,'emulator-5554')
    def test_closed_instance_is_launched_instead_of_rebooted(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(side_effect=['0,game,0,0,0,0,0',''])
            p.adb.run.return_value=b'1'
            self.assertTrue(p.restart())
            self.assertEqual(p.command.call_args_list[1].args,('launch','--index','0'))
    def test_cancel_during_boot_wait(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(side_effect=['0,game,0,0,0,-1,-1',''])
            p.stop.wait.return_value=True
            self.assertFalse(p.restart());p.adb.connect.assert_not_called()

    def test_full_restart_quits_only_selected_instance_and_waits_before_launch(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(side_effect=['0,game,1,2,1,99,88','','0,game,1,2,1,99,88','0,game,0,0,0,0,0',''])
            p.adb.run.return_value=b'1';self.assertTrue(p.restart(full=True))
            self.assertEqual([c.args for c in p.command.call_args_list],[('list2',),('quit','--index','0'),('list2',),('list2',),('launch','--index','0')])

    def test_full_restart_never_launches_a_duplicate_when_quit_is_unconfirmed(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(return_value='0,game,1,2,1,99,88')
            with self.assertRaisesRegex(RuntimeError,'종료 확인 실패'):p.restart(full=True)
            self.assertNotIn(('launch','--index','0'),[c.args for c in p.command.call_args_list])

    def test_full_restart_can_be_cancelled_before_launch(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=self.build(Path(folder));p.command=Mock(side_effect=['0,game,1,2,1,99,88','']);p.stop.wait.return_value=True
            self.assertFalse(p.restart(full=True));self.assertEqual(p.command.call_count,2)
