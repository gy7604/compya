"""Repair an absent LDPlayer 0 NAT relay without resetting shared ADB or the PC."""
import ctypes
from ctypes import wintypes as W
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from engine import SafetyStop

NETWORK='LdNatNetwork0'


class NetworkRecoveryStop(SafetyStop):
    pass


def fail():
    raise NetworkRecoveryStop('0번 통신 복구 대상 확인 실패 · 다른 프로세스 종료 없이 확인 필요')


def machine_fields(text):
    fields={}
    for line in text.splitlines():
        key,sep,value=line.partition('=')
        if not sep or key not in ('name','UUID','CfgFile','nic1','nat-network1','VMState'):continue
        if key in fields:fail()
        fields[key]=value.strip('"')
    return fields


def scoped_processes(rows,folder,home):
    """Reject ambiguous ownership; never select a process by name alone."""
    result={'nat':[],'dhcp':[]}
    for row in rows:
        name=row.get('Name')
        if name not in ('VBoxNetNAT.exe','VBoxNetDHCP.exe'):continue
        if not isinstance(row.get('CommandLine'),str):fail()
        try:args=[x.strip('"') for x in shlex.split(row['CommandLine'],posix=False)]
        except ValueError:fail()
        kind,flag=('nat','--network') if name=='VBoxNetNAT.exe' else ('dhcp','--comment')
        if NETWORK not in args:continue
        if (args.count(flag)!=1 or args.index(flag)+1>=len(args)
                or args[args.index(flag)+1]!=NETWORK or args.count(NETWORK)!=1):fail()
        expected=Path(folder)/name
        if not args or Path(args[0]).resolve()!=expected.resolve() or Path(row.get('ExecutablePath') or '').resolve()!=expected.resolve():fail()
        if kind=='dhcp':
            if args.count('--config')!=1 or args.index('--config')+1>=len(args):fail()
            if Path(args[args.index('--config')+1]).resolve()!=(Path(home)/(NETWORK+'-Dhcpd.config')).resolve():fail()
        if type(row.get('ProcessId')) is not int or row['ProcessId']<=0:fail()
        try:created=int(row['Created'])
        except (KeyError,ValueError,TypeError):fail()
        if created<=0:fail()
        result[kind].append(dict(pid=row['ProcessId'],created=created,path=str(expected.resolve())))
    if any(len(value)>1 for value in result.values()):fail()
    return result


class RepairBudget:
    def __init__(self,path,clock=time.time):self.path,self.clock=Path(path),clock
    def reserve(self):
        now=self.clock()
        if type(now) not in (int,float) or not math.isfinite(now) or now<0:
            raise NetworkRecoveryStop('0번 통신 복구 시각 확인 실패 · 자동 반복 없이 확인 필요')
        try:
            values=json.loads(self.path.read_text(encoding='utf8'))['attempts'] if self.path.exists() else []
            if (not isinstance(values,list) or any(type(t) not in (int,float) or not 0<=t<=now for t in values)
                    or values!=sorted(values)):raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):
            raise NetworkRecoveryStop('0번 통신 복구 기록 확인 실패 · 자동 반복 없이 확인 필요') from None
        values=[t for t in values if now-t<86400]
        if ((values and now-values[-1]<600) or sum(now-t<3600 for t in values)>=2 or len(values)>=6):
            raise NetworkRecoveryStop('0번 통신 복구 간격/한도 도달 · 반복 재시작 없이 확인 필요')
        values.append(now);self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(dict(attempts=values)),encoding='utf8');temp.replace(self.path)
        except OSError:
            raise NetworkRecoveryStop('0번 통신 복구 기록 저장 실패 · 프로세스 조작 없이 중지') from None


class WindowsNetwork:
    SCRIPT="""$ErrorActionPreference='Stop'
@(Get-CimInstance Win32_Process -Filter "Name='VBoxNetDHCP.exe' OR Name='VBoxNetNAT.exe'" | ForEach-Object {
    [pscustomobject]@{Name=$_.Name;ProcessId=[int]$_.ProcessId;ExecutablePath=$_.ExecutablePath;CommandLine=$_.CommandLine;Created=$_.CreationDate.ToFileTimeUtc().ToString()}
}) | ConvertTo-Json -Compress
"""
    def __init__(self,player):
        self.player=player
        self.folder=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'ldplayer9box'
        self.exe=self.folder/'VBoxManage.exe'
        self.home=Path(os.environ['USERPROFILE'])/'.Ld9VirtualBox'
        self.config=player.console.parent/'vms'/'leidian0'/'leidian.vbox'
        self.powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    def run(self,args):
        try:
            result=subprocess.run(args,capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise RuntimeError()
            return result.stdout.decode('utf8',errors='strict').lstrip('\ufeff')
        except (OSError,UnicodeError,RuntimeError,subprocess.TimeoutExpired):
            raise NetworkRecoveryStop('0번 통신 상태 확인/제어 실패 · 자동 반복 없이 확인 필요') from None
    def command(self,*args):return self.run([str(self.exe),*args])
    def guard(self):
        if self.player.index!=0:fail()
        try:
            machine=next(node for node in ET.parse(self.config).iter() if node.tag.rsplit('}',1)[-1]=='Machine')
            uuid=machine.attrib['uuid'].strip('{}').lower()
            if not re.fullmatch('[0-9a-f-]{36}',uuid):fail()
            fields=machine_fields(self.command('showvminfo','leidian0','--machinereadable'))
            if (fields.get('name')!='leidian0' or fields.get('UUID','').lower()!=uuid
                    or Path(fields.get('CfgFile','')).resolve()!=self.config.resolve()
                    or fields.get('nic1')!='natnetwork' or fields.get('nat-network1')!=NETWORK):fail()
            running=self.command('list','runningvms').strip().splitlines()
            allowed='"leidian0" {'+uuid+'}'
            if any(line.lower()!=allowed.lower() for line in running) or len(running)>1:fail()
            if fields.get('VMState') not in ('running','poweroff'):fail()
            return fields
        except (OSError,ValueError,KeyError,StopIteration,ET.ParseError):fail()
    def processes(self):
        try:
            text=self.run([str(self.powershell),'-NoProfile','-NonInteractive','-Command',
                           '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();'+self.SCRIPT])
            data=json.loads(text) if text.strip() else []
            if isinstance(data,dict):data=[data]
            if not isinstance(data,list) or any(not isinstance(row,dict) for row in data):fail()
            return scoped_processes(data,self.folder,self.home)
        except (ValueError,TypeError):fail()
    def inspect(self):
        self.guard()
        return self.processes()
    @contextmanager
    def locked(self):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,args,result in (
                ('CreateMutexW',[W.LPVOID,W.BOOL,W.LPCWSTR],W.HANDLE),
                ('WaitForSingleObject',[W.HANDLE,W.DWORD],W.DWORD),
                ('ReleaseMutex',[W.HANDLE],W.BOOL),('CloseHandle',[W.HANDLE],W.BOOL)):
            func=getattr(kernel,name);func.argtypes=args;func.restype=result
        handle=kernel.CreateMutexW(None,False,'Local\\CompyaLDPlayer0NetworkRepair')
        if not handle:fail()
        acquired=False
        try:
            acquired=kernel.WaitForSingleObject(handle,0) in (0,128)
            if not acquired:raise NetworkRecoveryStop('다른 0번 통신 복구 진행 중 · 중복 조작 없이 확인 필요')
            yield
        finally:
            if acquired:kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    def stop_network(self):
        self.guard()
        if self.processes()['nat']:raise NetworkRecoveryStop('0번 통신 중계가 다른 곳에서 시작됨 · 추가 조작 없이 확인 필요')
        self.command('natnetwork','stop','--netname',NETWORK)
    def stop_dhcp(self,record):
        self.guard()
        if record not in self.processes()['dhcp']:fail()
        terminate_verified_process(record,self.player.stop)
    def start_network(self):
        self.guard()
        if self.processes()['nat']:raise NetworkRecoveryStop('0번 통신 중계가 다른 곳에서 시작됨 · 중복 실행 없이 확인 필요')
        self.command('natnetwork','start','--netname',NETWORK)
    def vm_lease(self):
        from player_memory import WindowsMemory
        self.guard()
        return WindowsMemory(self.player).open_current()


@contextmanager
def verified_process(record,terminate=False):
    """Validate identity once and retain that process handle through any action."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    signatures={'OpenProcess':([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
        'CloseHandle':([W.HANDLE],W.BOOL),
        'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD),
        'QueryFullProcessImageNameW':([W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)],W.BOOL),
        'GetProcessTimes':([W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4,W.BOOL),
        'TerminateProcess':([W.HANDLE,W.UINT],W.BOOL)}
    for name,(args,result) in signatures.items():
        func=getattr(kernel,name);func.argtypes=args;func.restype=result
    handle=kernel.OpenProcess(0x1000|0x100000|(1 if terminate else 0),False,record['pid'])
    if not handle:fail()
    try:
        path=ctypes.create_unicode_buffer(32768);length=W.DWORD(len(path))
        times=[W.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(length)) or not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):fail()
        created=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
        # CIM timestamps have microsecond resolution; retain the original handle.
        if Path(path.value).resolve()!=Path(record['path']).resolve() or created//10!=record['created']//10:fail()
        yield kernel,handle
    finally:kernel.CloseHandle(handle)


def terminate_verified_process(record,stop):
    with verified_process(record,terminate=True) as (kernel,handle):
        if stop.is_set():return
        state=kernel.WaitForSingleObject(handle,0)
        if state==0:return
        if state!=258:fail()
        if not kernel.TerminateProcess(handle,0) or kernel.WaitForSingleObject(handle,5000)!=0:fail()


class NetworkRecovery:
    def __init__(self,player,backend,budget,emit):
        self.player,self.backend,self.budget,self.emit=player,backend,budget,emit
    def broken(self):return not self.backend.inspect()['nat']
    def repair_if_needed(self):
        if self.player.stop.is_set():return False
        try:self.player.adb.connect();return False
        except RuntimeError:pass
        if not self.broken():return False
        with self.backend.locked():return self.repair_locked()
    def repair_locked(self):
        if self.player.stop.is_set():return False
        self.budget.reserve()
        if self.player.stop.is_set():return False
        # Recheck after the durable reservation; an external repair wins the race.
        if not self.broken():return False
        self.emit('0번 통신 중계 미실행 확인 · 전용 통신망 자동 복구 시작')
        self.backend.stop_network()
        if self.player.stop.is_set():return False
        current=self.backend.inspect()
        if current['nat']:raise NetworkRecoveryStop('0번 통신 중계가 다른 곳에서 시작됨 · 추가 조작 없이 확인 필요')
        for record in current['dhcp']:
            if self.player.stop.is_set():return False
            self.backend.stop_dhcp(record)
        if self.player.stop.is_set():return False
        self.backend.start_network()
        for _ in range(10):
            if self.player.stop.is_set():return False
            if self.backend.inspect()['nat']:
                self.emit('0번 전용 통신망 복구 완료 · 정상 종료/실행 후 ADB 재연결')
                return True
            if self.player.stop.wait(.5):return False
        raise NetworkRecoveryStop('0번 통신 중계 실행 미확인 · 반복 재시작 없이 확인 필요')
    def vm_lease(self):return self.backend.vm_lease()


def configure_network_recovery(player,root,emit):
    if os.name!='nt' or player.index!=0:return
    if not isinstance(player.console,Path):return
    backend=WindowsNetwork(player)
    if not all(p.is_file() for p in (backend.exe,backend.powershell,backend.config)):
        emit('0번 통신 자동 복구 대기 · 설치 경로 확인 필요')
        return
    player.network=NetworkRecovery(player,backend,RepairBudget(Path(root)/'player_network_state.json'),emit)
