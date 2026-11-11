"""Replay incomplete crash-time inspection without touching the emulator."""
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from player_network import NETWORK, NetworkRecovery, NetworkRecoveryStop, RepairBudget, WindowsNetwork
from player_restart import PlayerRestart


class InspectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stop = threading.Event()
        self.stop.wait = Mock(return_value=False)
        self.player = Mock(index=0, console=self.root/'ldconsole.exe', stop=self.stop)
        self.player.adb.connect.side_effect = RuntimeError('device missing')
        self.backend = WindowsNetwork(self.player)
        self.backend.folder = self.root
        self.backend.home = self.root/'home'
        self.backend.config = self.root/'leidian.vbox'
        self.uuid = '20160302-aaaa-aaaa-0eee-000000000000'
        self.backend.config.write_text(f'<VirtualBox><Machine uuid="{{{self.uuid}}}"/></VirtualBox>')
        self.fields = (f'name="leidian0"\nUUID="{self.uuid}"\nCfgFile="{self.backend.config}"\n'
                       f'nic1="natnetwork"\nnat-network1="{NETWORK}"\nVMState="running"')
        self.backend.command = Mock(side_effect=lambda *args: self.fields if args[0] == 'showvminfo'
                                    else f'"leidian0" {{{self.uuid}}}')
        self.backend.locked = nullcontext
        self.backend.stop_network = Mock()
        self.backend.stop_dhcp = Mock()
        self.backend.start_network = Mock(side_effect=lambda: setattr(self.backend.run, 'side_effect', None)
                                         or setattr(self.backend.run, 'return_value', json.dumps([self.row()])))
        self.budget = RepairBudget(self.root/'state.json', lambda: 100000)
        self.repair = NetworkRecovery(self.player, self.backend, self.budget, Mock())

    def row(self):
        return dict(Name='VBoxNetNAT.exe', ProcessId=9, Created='1234560',
                    ExecutablePath=str(self.root/'VBoxNetNAT.exe'),
                    CommandLine=f'"{self.root/"VBoxNetNAT.exe"}" --network {NETWORK}')

    def assert_no_mutation(self):
        self.backend.stop_network.assert_not_called()
        self.backend.stop_dhcp.assert_not_called()
        self.backend.start_network.assert_not_called()
        self.assertFalse(self.budget.path.exists())

    def test_crashed_row_disappears_then_original_repair_completes(self):
        incomplete = self.row()
        incomplete['CommandLine'] = None
        self.backend.run = Mock(side_effect=[json.dumps([incomplete]), '[]', '[]', '[]'])
        self.assertTrue(self.repair.repair_if_needed())
        self.backend.start_network.assert_called_once()
        self.backend.stop_dhcp.assert_not_called()

    def test_persistent_incomplete_row_stops_with_specific_reason_without_mutation(self):
        incomplete = self.row()
        incomplete['CommandLine'] = None
        self.backend.run = Mock(return_value=json.dumps([incomplete]))
        with self.assertRaisesRegex(NetworkRecoveryStop, 'PROCESS_COMMAND_LINE'):
            self.repair.repair_if_needed()
        self.assertGreater(self.backend.run.call_count, 1)
        self.assertLessEqual(self.backend.run.call_count, 3)
        self.assert_no_mutation()

    def test_starting_vm_is_requeried_before_accepting_running_scope(self):
        original = self.fields
        count = 0
        def command(*args):
            nonlocal count
            if args[0] == 'showvminfo':
                count += 1
                return original.replace('"running"', '"starting"') if count == 1 else original
            return f'"leidian0" {{{self.uuid}}}'
        self.backend.command.side_effect = command
        self.backend.run = Mock(return_value='[]')
        self.assertEqual(self.backend.inspect(), dict(nat=[], dhcp=[]))
        self.assertEqual(count, 2)
        self.assert_no_mutation()

    def test_foreign_network_is_rejected_immediately_without_retry_or_mutation(self):
        self.fields = self.fields.replace(NETWORK, 'LdNatNetwork1')
        self.backend.run = Mock(return_value='[]')
        with self.assertRaisesRegex(NetworkRecoveryStop, 'VM_NETWORK'):
            self.repair.repair_if_needed()
        self.stop.wait.assert_not_called()
        self.backend.run.assert_not_called()
        self.assert_no_mutation()

    def test_stop_during_incomplete_snapshot_wait_prevents_repair(self):
        incomplete = self.row()
        incomplete['ExecutablePath'] = None
        self.backend.run = Mock(return_value=json.dumps([incomplete]))
        def cancel(timeout):
            self.stop.set()
            return True
        self.stop.wait.side_effect = cancel
        with self.assertRaisesRegex(NetworkRecoveryStop, 'INSPECTION_CANCELLED'):
            self.repair.repair_if_needed()
        self.assert_no_mutation()

    def test_requery_does_not_accept_foreign_image_after_incomplete_row(self):
        incomplete = self.row()
        incomplete['CommandLine'] = None
        foreign = self.row()
        foreign['ExecutablePath'] = str(self.root/'foreign.exe')
        self.backend.run = Mock(side_effect=[json.dumps([incomplete]), json.dumps([foreign])])
        with self.assertRaisesRegex(NetworkRecoveryStop, 'PROCESS_IMAGE_PATH'):
            self.repair.repair_if_needed()
        self.assertEqual(self.backend.run.call_count, 2)
        self.assert_no_mutation()

    def test_wrong_image_with_complete_metadata_is_not_retried(self):
        foreign = self.row()
        foreign['ExecutablePath'] = str(self.root/'foreign.exe')
        self.backend.run = Mock(return_value=json.dumps([foreign]))
        with self.assertRaisesRegex(NetworkRecoveryStop, 'PROCESS_IMAGE_PATH'):
            self.repair.repair_if_needed()
        self.backend.run.assert_called_once()
        self.stop.wait.assert_not_called()
        self.assert_no_mutation()

    def test_missing_creation_time_is_requeried_without_losing_other_network(self):
        incomplete = self.row()
        incomplete['Created'] = None
        other = self.row()
        other['CommandLine'] = other['CommandLine'].replace(NETWORK, 'LdNatNetwork1')
        self.backend.run = Mock(side_effect=[json.dumps([incomplete, other]), json.dumps([other])])
        self.assertTrue(self.repair.broken())
        self.assert_no_mutation()

    def test_malformed_snapshot_can_recover_to_a_fully_valid_live_nat(self):
        self.backend.run = Mock(side_effect=['{', json.dumps([self.row()])])
        self.assertFalse(self.repair.repair_if_needed())
        self.assert_no_mutation()

    def test_transient_query_error_is_retried_only_by_read_inspection(self):
        # Exercise run() with a failed external query, then a fresh full snapshot.
        self.backend.command.side_effect = None
        self.backend.command.return_value = None
        self.backend.command = lambda *args: WindowsNetwork.command(self.backend, *args)
        outputs = [Mock(returncode=1, stdout=b''),
                   Mock(returncode=0, stdout=self.fields.encode('utf8')),
                   Mock(returncode=0, stdout=f'"leidian0" {{{self.uuid}}}'.encode()),
                   Mock(returncode=0, stdout=b'[]')]
        with patch('player_network.subprocess.run', side_effect=outputs) as run:
            self.assertTrue(self.repair.broken())
        self.assertEqual(run.call_count, 4)
        self.assert_no_mutation()

    def test_uncertain_control_guard_never_retries_or_sends_stop_command(self):
        self.fields = self.fields.replace('"running"', '"starting"')
        self.backend.run = Mock(return_value='[]')
        with self.assertRaisesRegex(NetworkRecoveryStop, 'VM_TRANSITION'):
            WindowsNetwork.stop_network(self.backend)
        self.assertEqual([call.args for call in self.backend.command.call_args_list],
                         [('showvminfo', 'leidian0', '--machinereadable'), ('list', 'runningvms')])
        self.backend.run.assert_not_called()
        self.assert_no_mutation()

    def test_boot_probe_waits_out_partial_crash_snapshot_then_requests_repair(self):
        self.player.console.touch()
        self.player.adb.serial = '127.0.0.1:5555'
        player = PlayerRestart(self.player.console, 0, self.player.adb, self.stop)
        player.network = self.repair
        player.startup_state = Mock(return_value=True)
        incomplete = self.row()
        incomplete['CommandLine'] = None
        self.backend.run = Mock(side_effect=[json.dumps([incomplete]), '[]'])
        with patch('player_restart.time.monotonic', side_effect=[0, 0, 13]):
            with self.assertRaisesRegex(RuntimeError, '통신 중계 미실행'):
                player.wait_boot()
        self.assertTrue(player.network_fault)
        self.assert_no_mutation()

    def test_successful_requery_cancelled_during_read_cannot_stop_network(self):
        incomplete = self.row()
        incomplete['CommandLine'] = None
        snapshots = iter(['[]', json.dumps([incomplete]), '[]'])
        reads = 0
        def read(*args):
            nonlocal reads
            reads += 1
            if reads == 3:
                self.stop.set()
            return next(snapshots, '[]')
        self.backend.run = Mock(side_effect=read)
        self.backend.stop_network = lambda: WindowsNetwork.stop_network(self.backend)
        with self.assertRaisesRegex(NetworkRecoveryStop, 'INSPECTION_CANCELLED'):
            self.repair.repair_if_needed()
        self.assertFalse(any(call.args[0] == 'natnetwork' for call in self.backend.command.call_args_list))
        self.backend.start_network.assert_not_called()

    def test_cancel_during_last_control_query_prevents_stop_and_start_commands(self):
        for action in (WindowsNetwork.stop_network, WindowsNetwork.start_network):
            with self.subTest(action=action.__name__):
                self.stop.clear()
                self.backend.command.reset_mock()
                self.backend.run = Mock(side_effect=lambda *args: self.stop.set() or '[]')
                action(self.backend)
                self.assertFalse(any(call.args[0] == 'natnetwork'
                                     for call in self.backend.command.call_args_list))

    def test_cancel_during_last_dhcp_query_never_opens_termination_path(self):
        record = dict(pid=8, created=1234560, path='dhcp.exe')
        self.backend.processes = Mock(side_effect=lambda: self.stop.set() or dict(dhcp=[record]))
        with patch('player_network.terminate_verified_process') as terminate:
            WindowsNetwork.stop_dhcp(self.backend, record)
        terminate.assert_not_called()

    def test_incomplete_first_row_does_not_hide_later_foreign_target(self):
        incomplete = self.row()
        incomplete['CommandLine'] = None
        foreign = self.row()
        foreign['ExecutablePath'] = str(self.root/'foreign.exe')
        self.backend.run = Mock(side_effect=[json.dumps([incomplete, foreign]), '[]', '[]', '[]'])
        with self.assertRaisesRegex(NetworkRecoveryStop, 'PROCESS_IMAGE_PATH'):
            self.repair.repair_if_needed()
        self.stop.wait.assert_not_called()
        self.assert_no_mutation()

    def test_incomplete_first_row_does_not_hide_duplicate_scoped_processes(self):
        incomplete = self.row()
        incomplete['CommandLine'] = None
        self.backend.run = Mock(side_effect=[json.dumps([incomplete, self.row(), self.row()]), '[]', '[]', '[]'])
        with self.assertRaisesRegex(NetworkRecoveryStop, 'PROCESS_DUPLICATE'):
            self.repair.repair_if_needed()
        self.stop.wait.assert_not_called()
        self.assert_no_mutation()


if __name__ == '__main__':
    unittest.main()
