import subprocess
import unittest
from unittest.mock import patch
from adb import ADB


class ADBTests(unittest.TestCase):
    @patch('adb.subprocess.run')
    def test_tap_targets_only_selected_device(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b'', b'')
        ADB('adb.exe', '127.0.0.1:5555').tap((30, 40))
        self.assertEqual(run.call_args.args[0], ['adb.exe', '-s', '127.0.0.1:5555', 'shell', 'input', 'tap', '30', '40'])

    @patch('adb.subprocess.run')
    def test_connection_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, b'', b'offline')
        with self.assertRaises(RuntimeError):
            ADB('adb', 'emulator-5554').connect()

    @patch('adb.subprocess.run', side_effect=subprocess.TimeoutExpired('adb', 5))
    def test_timeout(self, run):
        with self.assertRaises(RuntimeError):
            ADB('adb', 'emulator-5554').screenshot()
