import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from player_network import (NETWORK,NetworkRecoveryStop,RepairBudget,NetworkRecovery,
                            WindowsNetwork,scoped_processes,machine_fields,terminate_verified_process)
from player_restart import PlayerRestart


class Backend:
    def __init__(self):
        self.nat=[];self.dhcp=[dict(pid=8,created=1234560,path='dhcp.exe')];self.calls=[]
    def inspect(self):return dict(nat=list(self.nat),dhcp=list(self.dhcp))
    def locked(self):return nullcontext()
    def stop_network(self):self.calls.append('stop')
    def stop_dhcp(self,record):
        assert record==self.dhcp[0];self.calls.append(('dhcp',record['pid']));self.dhcp=[]
    def start_network(self):self.calls.append('start');self.nat=[dict(pid=9)]
    def vm_lease(self):return None


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.stop=threading.Event();self.stop.wait=Mock(return_value=False)
        self.adb=Mock();self.adb.connect.side_effect=RuntimeError('device missing')
        self.player=Mock(index=0,stop=self.stop,adb=self.adb)
        self.backend=Backend();self.now=100000
        self.budget=RepairBudget(self.root/'state.json',lambda:self.now)
        self.repair=NetworkRecovery(self.player,self.backend,self.budget,Mock())

    def test_known_broken_relay_repairs_only_orphan_dhcp_and_reserves_first(self):
        original=self.backend.stop_network
        def stop():
            self.assertEqual(json.loads(self.budget.path.read_text())['attempts'],[self.now]);original()
        self.backend.stop_network=stop
        self.assertTrue(self.repair.repair_if_needed())
        self.assertEqual(self.backend.calls,['stop',('dhcp',8),'start'])

    def test_healthy_adb_never_inspects_or_restarts_network(self):
        self.adb.connect.side_effect=None;self.backend.inspect=Mock()
        self.assertFalse(self.repair.repair_if_needed())
        self.backend.inspect.assert_not_called();self.assertEqual(self.backend.calls,[])
        self.assertFalse(self.budget.path.exists())

    def test_live_nat_with_missing_adb_is_not_assumed_broken(self):
        self.backend.nat=[dict(pid=9)]
        self.assertFalse(self.repair.repair_if_needed());self.assertEqual(self.backend.calls,[])

    def test_cancel_before_detection_has_no_commands_or_budget(self):
        self.stop.set();self.assertFalse(self.repair.repair_if_needed())
        self.adb.connect.assert_not_called();self.assertFalse(self.budget.path.exists())

    def test_cancel_between_steps_sends_no_later_commands(self):
        self.backend.stop_network=lambda:self.stop.set()
        self.assertFalse(self.repair.repair_if_needed());self.assertEqual(self.backend.calls,[])

    def test_external_repair_wins_race_after_reservation(self):
        self.repair.broken=Mock(side_effect=[True,False])
        self.assertFalse(self.repair.repair_if_needed());self.assertEqual(self.backend.calls,[])

    def test_new_relay_after_stop_aborts_without_killing_or_starting_again(self):
        self.backend.stop_network=lambda:self.backend.nat.append(dict(pid=42))
        with self.assertRaises(NetworkRecoveryStop):self.repair.repair_if_needed()
        self.assertNotIn('start',self.backend.calls)

    def test_missing_relay_after_start_stops_without_repeating_start(self):
        self.backend.start_network=lambda:self.backend.calls.append('start')
        with self.assertRaises(NetworkRecoveryStop):self.repair.repair_if_needed()
        self.assertEqual(self.backend.calls.count('start'),1)

    def test_budget_survives_program_restart_and_throttles(self):
        self.budget.reserve()
        with self.assertRaises(NetworkRecoveryStop):RepairBudget(self.budget.path,lambda:self.now+599).reserve()
        RepairBudget(self.budget.path,lambda:self.now+600).reserve()
        with self.assertRaises(NetworkRecoveryStop):RepairBudget(self.budget.path,lambda:self.now+1200).reserve()
        RepairBudget(self.budget.path,lambda:self.now+3600).reserve()

    def test_daily_budget_cannot_be_bypassed_by_reopening_program(self):
        for hour in range(6):RepairBudget(self.budget.path,lambda:100000+hour*3600).reserve()
        with self.assertRaises(NetworkRecoveryStop):RepairBudget(self.budget.path,lambda:100000+7*3600).reserve()
        RepairBudget(self.budget.path,lambda:100000+86400).reserve()

    def test_invalid_clock_history_and_save_failure_do_not_mutate_network(self):
        for values in (dict(attempts=[self.now+1]),dict(attempts=[3,2]),dict(attempts=[True]),dict(attempts='bad')):
            self.budget.path.write_text(json.dumps(values))
            with self.assertRaises(NetworkRecoveryStop):self.repair.repair_if_needed()
            self.assertEqual(self.backend.calls,[])
        self.budget.path.unlink()
        with patch.object(Path,'write_text',side_effect=OSError('full disk')):
            with self.assertRaises(NetworkRecoveryStop):self.repair.repair_if_needed()
        self.assertEqual(self.backend.calls,[])

    def test_nonfinite_clock_does_not_authorize_first_repair(self):
        for now in (float('nan'),float('inf'),-1,True):
            with self.subTest(now=now):
                self.budget.clock=lambda:now
                with self.assertRaises(NetworkRecoveryStop):self.repair.repair_if_needed()
                self.assertFalse(self.budget.path.exists());self.assertEqual(self.backend.calls,[])

    def test_scope_rejection_never_reserves_or_mutates(self):
        self.backend.inspect=Mock(side_effect=NetworkRecoveryStop('foreign VM'))
        with self.assertRaises(NetworkRecoveryStop):self.repair.repair_if_needed()
        self.assertEqual(self.backend.calls,[]);self.assertFalse(self.budget.path.exists())

    def test_repair_forces_normal_quit_then_launch_and_not_vm_reboot(self):
        console=self.root/'ldconsole.exe';console.touch();self.adb.serial='127.0.0.1:5555'
        player=PlayerRestart(str(console),0,self.adb,self.stop)
        player.network=self.repair
        player.command=Mock(side_effect=['0,zero,1,2,1,10,20','','0,zero,0,0,0,-1,-1',''])
        player.wait_boot=Mock(return_value=True)
        self.assertTrue(player.restart())
        self.assertEqual([c.args for c in player.command.call_args_list],
                         [('list2',),('quit','--index','0'),('list2',),('launch','--index','0')])

    def test_normal_offline_memory_cleanup_does_not_spend_network_budget(self):
        console=self.root/'ldconsole.exe';console.touch();self.adb.serial='127.0.0.1:5555'
        player=PlayerRestart(str(console),0,self.adb,self.stop);player.network=self.repair
        self.repair.repair_if_needed=Mock(return_value=False)
        player.command=Mock(side_effect=['0,zero,0,0,0,-1,-1','']);player.wait_boot=Mock(return_value=True)
        self.assertTrue(player.restart());self.repair.repair_if_needed.assert_not_called()

    def test_known_network_failure_is_repaired_even_after_memory_cleanup_closed_vm(self):
        console=self.root/'ldconsole.exe';console.touch();self.adb.serial='127.0.0.1:5555'
        player=PlayerRestart(str(console),0,self.adb,self.stop);player.network=self.repair;player.network_fault=True
        player.command=Mock(side_effect=['0,zero,0,0,0,-1,-1','']);player.wait_boot=Mock(return_value=True)
        self.assertTrue(player.restart());self.assertIn('start',self.backend.calls)
        self.assertFalse(player.network_fault)

    def test_confirmed_absent_relay_shortens_boot_wait_without_any_repair_from_probe(self):
        console=self.root/'ldconsole.exe';console.touch();self.adb.serial='127.0.0.1:5555'
        player=PlayerRestart(str(console),0,self.adb,self.stop);player.network=Mock()
        player.network.broken.return_value=True;player.startup_state=Mock(return_value=True)
        with patch('player_restart.time.monotonic',side_effect=[0,0,13]):
            with self.assertRaisesRegex(RuntimeError,'통신 중계 미실행'):player.wait_boot()
        self.assertTrue(player.network_fault);player.network.repair_if_needed.assert_not_called()

    def test_full_quit_keeps_original_process_handle_until_it_has_really_exited(self):
        console=self.root/'ldconsole.exe';console.touch();self.adb.serial='127.0.0.1:5555'
        player=PlayerRestart(str(console),0,self.adb,self.stop);player.network=Mock()
        player.network.repair_if_needed.return_value=True
        lease=Mock();lease.alive.side_effect=[True,False];player.network.vm_lease.return_value=lease
        player.command=Mock(side_effect=['0,z,1,2,1,10,20','','0,z,0,0,0,-1,-1','0,z,0,0,0,-1,-1',''])
        player.wait_boot=Mock(return_value=True);self.assertTrue(player.restart())
        self.assertEqual(lease.alive.call_count,2);lease.close.assert_called_once()
        self.assertEqual(player.command.call_args_list[-1].args,('launch','--index','0'))

    def test_user_stop_during_vm_identity_check_prevents_quit_and_launch(self):
        console=self.root/'ldconsole.exe';console.touch();self.adb.serial='127.0.0.1:5555'
        player=PlayerRestart(str(console),0,self.adb,self.stop);player.network=Mock()
        player.network.repair_if_needed.return_value=True;lease=Mock()
        player.network.vm_lease.side_effect=lambda:self.stop.set() or lease
        player.command=Mock(return_value='0,z,1,2,1,10,20')
        self.assertFalse(player.restart());player.command.assert_called_once_with('list2')
        lease.close.assert_called_once()


class TerminalFailureTests(unittest.TestCase):
    def test_memory_cleanup_scope_failure_is_terminal_and_not_retried_ten_times(self):
        from player_memory import run_cleanup
        from recovery import RecoveryMachine
        from test_recovery import fixture
        records,config=fixture();config['max_launches']=10
        recovery=RecoveryMachine(config,records);guard=Mock();stop=threading.Event()
        guard.recycle.side_effect=NetworkRecoveryStop('foreign VM')
        with self.assertRaises(NetworkRecoveryStop):run_cleanup(guard,recovery,Mock(),stop,Mock())
        guard.recycle.assert_called_once();self.assertEqual(recovery.launches,0)

    def test_startup_network_scope_failure_stops_and_notifies_without_fake_launch_count(self):
        from app import App
        from test_recovery import fixture
        from notifications import default_notifications
        records,recovery=fixture();recovery.update(target='league',start_player=True,max_launches=10)
        app=App.__new__(App);app.stop=threading.Event();app.emit=Mock();app.dispatch_notification=Mock()
        app.registry=Mock();app.registry.resolve.side_effect=lambda value:value
        adb=Mock();player=Mock(index=0);player.ensure_started.side_effect=NetworkRecoveryStop('unsafe network scope')
        notifications=dict(default_notifications(),enabled=True,watch=['goal'],topic='test_network_recovery_only')
        def matcher(*args,**kwargs):
            m=Mock();m.find.return_value=(False,(0,0),.1,(720,1280));return m
        with tempfile.TemporaryDirectory() as folder,patch('app.ROOT',Path(folder)),patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),patch('player_restart.PlayerRestart',return_value=player),patch('app.configure_network_recovery'):
            app.run_session(dict(buttons=records,recovery=recovery,notifications=notifications,dry=False,adb='adb',serial='127.0.0.1:5555'))
        player.restart.assert_not_called();adb.launch_app.assert_not_called();adb.tap.assert_not_called()
        app.dispatch_notification.assert_called_once()
        self.assertNotIn('10회',app.dispatch_notification.call_args.args[1])


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.home=self.folder/'home'
    def row(self,name='VBoxNetDHCP.exe'):
        flag='--comment' if 'DHCP' in name else '--network'
        extra=f' --config "{self.home/(NETWORK+"-Dhcpd.config")}"' if 'DHCP' in name else ''
        return dict(Name=name,ProcessId=8,ExecutablePath=str(self.folder/name),Created='1234560',
                    CommandLine=f'"{self.folder/name}" {flag} {NETWORK}'+extra)

    def test_only_exact_network_and_executable_paths_are_selected(self):
        rows=[self.row(),self.row('VBoxNetNAT.exe')]
        selected=scoped_processes(rows,self.folder,self.home)
        self.assertEqual(len(selected['dhcp']),1);self.assertEqual(len(selected['nat']),1)
        other=self.row();other['CommandLine']=other['CommandLine'].replace(NETWORK,'LdNatNetwork1')
        self.assertEqual(scoped_processes([other],self.folder,self.home)['dhcp'],[])

    def test_same_name_wrong_path_config_or_duplicate_process_is_rejected(self):
        for field,value in [('ExecutablePath',str(self.folder/'other.exe')),('CommandLine','bad '+NETWORK),
                            ('Created','bad'),('ProcessId',-1),('CommandLine',None)]:
            row=self.row();row[field]=value
            with self.assertRaises(NetworkRecoveryStop):scoped_processes([row],self.folder,self.home)
        row=self.row();row['CommandLine']=row['CommandLine'].replace('-Dhcpd.config','-other.config')
        with self.assertRaises(NetworkRecoveryStop):scoped_processes([row],self.folder,self.home)
        with self.assertRaises(NetworkRecoveryStop):scoped_processes([self.row(),self.row()],self.folder,self.home)

    def backend(self):
        player=Mock(index=0,console=self.folder/'ldconsole.exe');backend=WindowsNetwork(player)
        backend.config=self.folder/'leidian.vbox';backend.config.write_text('<VirtualBox><Machine uuid="{20160302-aaaa-aaaa-0eee-000000000000}"/></VirtualBox>')
        self.fields=f'name="leidian0"\nUUID="20160302-aaaa-aaaa-0eee-000000000000"\nCfgFile="{backend.config}"\nnic1="natnetwork"\nnat-network1="{NETWORK}"\nVMState="running"'
        backend.command=Mock(side_effect=lambda *args:self.fields if args[0]=='showvminfo' else '"leidian0" {20160302-aaaa-aaaa-0eee-000000000000}')
        return backend

    def test_foreign_vm_unknown_identity_or_network_is_rejected_before_mutation(self):
        for key,value in [('name','other'),('UUID','ffffffff-aaaa-aaaa-0eee-000000000000'),
                          ('CfgFile','other.vbox'),('nat-network1','LdNatNetwork1'),('VMState','saving')]:
            backend=self.backend()
            self.fields='\n'.join(f'{key}="{value}"' if line.startswith(key+'=') else line for line in self.fields.splitlines())
            with self.assertRaises(NetworkRecoveryStop):backend.guard()
        backend=self.backend();backend.command.side_effect=[self.fields,'"other" {foreign}']
        with self.assertRaises(NetworkRecoveryStop):backend.guard()

    def test_correct_running_and_poweroff_zero_are_accepted(self):
        backend=self.backend();self.assertEqual(backend.guard()['name'],'leidian0')
        self.fields=self.fields.replace('"running"','"poweroff"');backend.command.side_effect=lambda *args:self.fields if args[0]=='showvminfo' else ''
        self.assertEqual(backend.guard()['VMState'],'poweroff')

    def test_repeated_unrelated_vbox_fields_do_not_hide_duplicate_scope_fields(self):
        self.assertEqual(machine_fields('Guest=1\nGuest=2\nname="leidian0"'),{'name':'leidian0'})
        with self.assertRaises(NetworkRecoveryStop):machine_fields('name="leidian0"\nname="other"')

    def test_current_dhcp_identity_is_rechecked_immediately_before_termination(self):
        backend=self.backend();backend.processes=Mock(return_value=dict(dhcp=[]))
        with patch('player_network.terminate_verified_process') as terminate:
            with self.assertRaises(NetworkRecoveryStop):backend.stop_dhcp(dict(pid=8,created=1,path='old'))
            terminate.assert_not_called()


class ProcessIdentityTests(unittest.TestCase):
    def setup_kernel(self,path='dhcp.exe',created=1234560):
        kernel=Mock();kernel.OpenProcess.return_value=99;kernel.WaitForSingleObject.side_effect=[258,0]
        def image(handle,flags,buffer,length):buffer.value=str(Path(path).resolve());return True
        def times(handle,a,b,c,d):a._obj.dwLowDateTime=created&0xffffffff;a._obj.dwHighDateTime=created>>32;return True
        kernel.QueryFullProcessImageNameW.side_effect=image;kernel.GetProcessTimes.side_effect=times
        return kernel,dict(pid=8,created=1234560,path=str(Path('dhcp.exe').resolve()))
    def test_pid_reuse_or_image_change_never_terminates(self):
        for path,created in [('other.exe',1234560),('dhcp.exe',1234570)]:
            kernel,record=self.setup_kernel(path,created)
            with patch('player_network.ctypes.WinDLL',return_value=kernel):
                with self.assertRaises(NetworkRecoveryStop):terminate_verified_process(record,threading.Event())
            kernel.TerminateProcess.assert_not_called();kernel.CloseHandle.assert_called_once_with(99)
    def test_stop_before_kill_and_already_exited_process_are_preserved(self):
        for stopped in (False,True):
            kernel,record=self.setup_kernel();kernel.WaitForSingleObject.side_effect=[0]
            stop=threading.Event()
            if stopped:stop.set()
            with patch('player_network.ctypes.WinDLL',return_value=kernel):terminate_verified_process(record,stop)
            kernel.TerminateProcess.assert_not_called();kernel.CloseHandle.assert_called_once()
    def test_same_validated_handle_is_used_and_waited_before_close(self):
        kernel,record=self.setup_kernel()
        with patch('player_network.ctypes.WinDLL',return_value=kernel):terminate_verified_process(record,threading.Event())
        kernel.TerminateProcess.assert_called_once_with(99,0)
        self.assertEqual(kernel.WaitForSingleObject.call_args_list[-1].args,(99,5000))
        kernel.CloseHandle.assert_called_once_with(99)

    def test_unknown_process_wait_status_does_not_authorize_termination(self):
        kernel,record=self.setup_kernel();kernel.WaitForSingleObject.side_effect=[0xffffffff]
        with patch('player_network.ctypes.WinDLL',return_value=kernel):
            with self.assertRaises(NetworkRecoveryStop):terminate_verified_process(record,threading.Event())
        kernel.TerminateProcess.assert_not_called();kernel.CloseHandle.assert_called_once_with(99)
