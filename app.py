import json
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from adb import ADB
from engine import SafetyStop
from rules import RuleStore, WorkflowMachine, validate_rules
from registry import Registry
from version import VERSION
from speed import MODES, DEFAULT_MODE, speed_settings
from clicks import click_point
from ranking import RankingStore, RankingMachine, default_ranking, validate_ranking
from notifications import NotificationStore, NotificationDetector, default_notifications, validate_notifications, send_notification
from progress import ProgressStore, ProgressClicker, default_progress, validate_progress

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


class App:
    def __init__(self, root):
        self.root = root
        root.title(f'V26 투구 보조 v{VERSION} · 타격 OFF / 투구 ON / 풀 플레이')
        if (ROOT/'assets'/'app.ico').exists():
            root.iconbitmap(str(ROOT/'assets'/'app.ico'))
        root.geometry('1000x760')
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.updating = False
        self.update_worker = None
        self.next_release = None
        self.fields = {}
        form = ttk.Frame(root, padding=12)
        form.pack(fill='x')
        defaults = [('adb', 'ADB 실행 파일', 'adb'), ('serial', '장치 주소 / serial', '127.0.0.1:5555'),
                    ('games', '랭킹 챌린지 목표 연속 경기 횟수', '1')]
        try:
            saved = json.loads((ROOT/'last_settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        self.registry = Registry(ROOT)
        self.records = self.registry.load(saved)
        self.rule_store = RuleStore(ROOT)
        self.rules = self.rule_store.load(self.records)
        self.progress_store = ProgressStore(ROOT)
        self.progress = self.progress_store.load()
        self.notification_store = NotificationStore(ROOT)
        self.notifications = self.notification_store.load()
        self.notification_manager = None
        self.ranking_store=RankingStore(ROOT)
        self.ranking=self.ranking_store.load()
        self.ranking_manager=None
        self.manager = None
        self.rule_manager = None
        self.progress_manager = None
        for row, (key, label, value) in enumerate(defaults):
            value = saved.get(key, value)
            if isinstance(value, (list, tuple)):
                value = ','.join(map(str, value))
            if value is None:
                value = '' 
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=4)
            v = tk.StringVar(value=value)
            self.fields[key] = v
            ttk.Entry(form, textvariable=v, width=55).grid(row=row, column=1, sticky='ew')
        mode = saved.get('speed', DEFAULT_MODE)
        self.fields['speed'] = tk.StringVar(value=mode if mode in MODES else DEFAULT_MODE)
        ttk.Label(form, text='인식 반응속도').grid(row=0, column=2, padx=8)
        ttk.Combobox(form, textvariable=self.fields['speed'], values=list(MODES), state='readonly', width=22).grid(row=1, column=2, padx=8)
        ttk.Button(form,text='진행 보조 클릭 설정',command=self.manage_progress).grid(row=2,column=2,padx=8)
        ttk.Label(form, text='일반 버튼 · 조건 규칙: 이미지 대기 시간 제한 없음').grid(row=3, column=0, columnspan=2, sticky='w')
        ttk.Button(form,text='휴대폰 알림 설정',command=self.manage_notifications).grid(row=3,column=2,padx=8)
        self.button_summary = tk.StringVar(value=f'등록 {len(self.records)}개 / 사용 {sum(r["enabled"] for r in self.records)}개')
        ttk.Button(form, text='버튼 관리 · 추가 / 수정 / 이미지 교체', command=self.manage).grid(row=4, column=0, padx=0, pady=12)
        ttk.Label(form, textvariable=self.button_summary).grid(row=4, column=1, sticky='w')
        ttk.Button(form, text='조건 규칙 관리', command=self.manage_rules).grid(row=4, column=2, padx=8)
        self.capturing = False
        self.dry = tk.BooleanVar(value=True)
        self.confirm = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text='모의 실행 (탭 전송 안 함)', variable=self.dry).grid(row=5, column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(form, text='게임에서 타격 OFF · 투구 ON · 풀 플레이를 직접 설정했습니다.', variable=self.confirm).grid(row=6, column=0, columnspan=3, sticky='w')
        ttk.Label(form,text='랭킹: 연속 경기 결과 화면을 1회로 집계 · 시작마다 0회로 초기화').grid(row=7,column=0,columnspan=2,sticky='w')
        ttk.Button(form,text='랭킹 챌린지 설정',command=self.manage_ranking).grid(row=7,column=2,padx=8)
        update_bar = ttk.Frame(root)
        update_bar.pack(fill='x', padx=12, pady=5)
        self.update_status = tk.StringVar(value=f'현재 v{VERSION} · 시작 시 업데이트 확인')
        ttk.Label(update_bar, textvariable=self.update_status).pack(side='left')
        ttk.Button(update_bar, text='업데이트 확인', command=self.check_updates).pack(side='right')
        self.install_button = ttk.Button(update_bar, text='업데이트 설치', state='disabled', command=self.install_update)
        self.install_button.pack(side='right', padx=8)
        buttons = ttk.Frame(root)
        buttons.pack(fill='x', padx=12)
        self.start_button = ttk.Button(buttons, text='시작', command=self.start)
        self.start_button.pack(side='left')
        ttk.Button(buttons, text='중지 (Esc)', command=self.stop.set).pack(side='left', padx=8)
        self.status = tk.StringVar(value='대기 중 · 기본 모의 실행')
        ttk.Label(buttons, textvariable=self.status).pack(side='left')
        self.log = tk.Text(root, height=18, state='disabled')
        self.log.pack(fill='both', expand=True, padx=12, pady=12)
        (ROOT/'logs').mkdir(exist_ok=True)
        self.logger = logging.getLogger('macro')
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(ROOT/'logs'/'macro.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(handler)
        root.bind('<Escape>', lambda e: self.stop.set())
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.closing = False
        root.after(100, self.poll)
        if '--self-test' not in sys.argv:
            root.after(1000, self.check_updates)

    def check_updates(self):
        if self.closing or self.updating or (self.update_worker and self.update_worker.is_alive()):
            return
        self.update_status.set('새 버전 확인 중…')
        def task():
            try:
                from updates import check_latest
                self.events.put(('update_checked', check_latest()))
            except Exception as e:
                self.events.put(('update_error', str(e)))
        self.update_worker = threading.Thread(target=task, daemon=True)
        self.update_worker.start()

    def install_update(self):
        if not self.next_release or self.updating:
            return
        if (self.ranking_manager and self.ranking_manager.window.winfo_exists()) or (self.notification_manager and self.notification_manager.window.winfo_exists()) or self.capturing or (self.worker and self.worker.is_alive()) or (self.manager and self.manager.window.winfo_exists()) or (self.rule_manager and self.rule_manager.window.winfo_exists()) or (self.progress_manager and self.progress_manager.window.winfo_exists()):
            messagebox.showinfo('업데이트', '매크로를 중지하고 버튼 관리 창을 닫은 뒤 업데이트하세요.')
            return
        if not getattr(sys, 'frozen', False) or not (ROOT/'UpdateHelper.exe').is_file():
            messagebox.showinfo('업데이트', '자동 설치는 Windows 실행 파일 버전에서 사용할 수 있습니다.')
            return
        if not messagebox.askyesno('업데이트 설치', f'{self.next_release["tag"]} 버전을 설치할까요?\n버튼 이미지와 설정은 유지되며 다운로드 후 프로그램이 재시작됩니다.'):
            return
        try:
            self.registry.save(self.records)
            self.rule_store.save(self.rules, self.records)
            self.progress_store.save(self.progress, self.records)
            self.notification_store.save(self.notifications,self.records)
            self.ranking_store.save(self.ranking,self.records)
            (ROOT/'last_settings.json').write_text(json.dumps({k: v.get() for k, v in self.fields.items()}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            messagebox.showerror('설정 저장 실패', str(e))
            return
        self.updating = True
        self.start_button.configure(state='disabled')
        self.install_button.configure(state='disabled')
        release = dict(self.next_release)
        def task():
            try:
                from updates import stage_update
                previous = -1
                def progress(value):
                    nonlocal previous
                    if value != previous:
                        self.events.put(('update_progress', value))
                        previous = value
                self.events.put(('update_staged', stage_update(ROOT, release, progress=progress)))
            except Exception as e:
                self.events.put(('update_failed', str(e)))
        self.update_worker = threading.Thread(target=task, daemon=True)
        self.update_worker.start()

    def update_event(self, msg):
        kind, value = msg
        if kind == 'update_checked':
            self.next_release = value
            self.update_status.set(f'새 버전 {value["tag"]} · 업데이트 설치를 누르세요' if value else f'현재 v{VERSION} · 새 배포 없음')
            self.install_button.configure(state='normal' if value else 'disabled')
        elif kind == 'update_error':
            self.update_status.set('업데이트 확인 불가 · 프로그램은 계속 사용할 수 있습니다.')
            self.emit('업데이트 확인 실패 · ' + value)
        elif kind == 'update_progress':
            self.update_status.set(f'업데이트 다운로드 {value}% · 창을 열어 두세요')
        elif kind == 'update_staged':
            try:
                from updates import launch_helper
                launch_helper(ROOT, value)
                self.updating = False
                self.close()
            except Exception as e:
                self.update_event(('update_failed', str(e)))
        elif kind == 'update_failed':
            self.updating = False
            self.update_status.set('업데이트 실패 · 기존 프로그램 유지')
            self.install_button.configure(state='normal')
            messagebox.showerror('업데이트 실패', value)

    def manage(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('버튼 관리', '실행을 중지한 뒤 버튼을 수정하세요.')
            return
        from button_ui import ButtonManager
        if self.manager and self.manager.window.winfo_exists():
            self.manager.window.lift()
            return
        self.manager = ButtonManager(self)

    def request_capture(self, callback):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('캡처', '실행을 중지하고 다른 캡처가 끝난 뒤 진행하세요.')
            return
        self.capturing = True
        adb_path, serial = self.fields['adb'].get(), self.fields['serial'].get()
        self.status.set('ADB 화면 캡처 중…')
        def task():
            try:
                adb = ADB(adb_path, serial)
                adb.connect()
                self.events.put(('capture', callback, adb.screenshot()))
            except Exception as e:
                self.events.put(('capture_error', str(e)))
        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def manage_rules(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('조건 규칙', '실행을 중지한 뒤 규칙을 수정하세요.')
            return
        from rule_ui import RuleManager
        if self.rule_manager and self.rule_manager.window.winfo_exists():
            self.rule_manager.window.lift()
            return
        self.rule_manager = RuleManager(self)

    def emit(self, text):
        self.logger.info(text)
        self.events.put(text)

    def manage_progress(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('진행 보조 클릭','실행을 중지한 뒤 설정하세요.')
            return
        from progress_ui import ProgressManager
        if self.progress_manager and self.progress_manager.window.winfo_exists():
            self.progress_manager.window.lift()
            return
        self.progress_manager=ProgressManager(self)

    def manage_ranking(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('랭킹 챌린지','실행을 중지한 뒤 설정하세요.');return
        from ranking_ui import RankingManager
        if self.ranking_manager and self.ranking_manager.window.winfo_exists():
            self.ranking_manager.window.lift();return
        self.ranking_manager=RankingManager(self)

    def manage_notifications(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('휴대폰 알림','실행을 중지한 뒤 설정하세요.')
            return
        from notification_ui import NotificationManager
        if self.notification_manager and self.notification_manager.window.winfo_exists():
            self.notification_manager.window.lift();return
        self.notification_manager=NotificationManager(self)

    def dispatch_notification(self,config,text):
        def task():
            try:
                send_notification(config,text)
                self.emit('휴대폰 알림 전송 성공')
            except Exception:
                self.emit('휴대폰 알림 전송 실패 · 인터넷과 알림 설정을 확인하세요. 자동 재전송하지 않습니다.')
        threading.Thread(target=task,daemon=True).start()

    def start(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            return
        try:
            config = {k: v.get() for k, v in self.fields.items()}
            config['games'] = int(config['games'])
            speed_settings(config['speed'])
            if not 1 <= config['games'] <= 9999:
                raise ValueError('목표 연속 경기 횟수는 1~9999입니다.')
            import copy
            config['buttons'] = copy.deepcopy(self.records)
            config['rules'] = copy.deepcopy(self.rules)
            config['progress'] = copy.deepcopy(self.progress)
            config['notifications'] = copy.deepcopy(self.notifications)
            config['ranking']=copy.deepcopy(self.ranking)
            validate_ranking(self.ranking,self.records)
            validate_notifications(self.notifications,self.records)
            validate_rules(self.rules, self.records)
            validate_progress(self.progress,self.records)
            if not any(r['enabled'] and r.get('kind', 'normal') == 'normal' for r in self.records) and not any(r['enabled'] for r in self.rules) and not self.progress['enabled'] and not self.notifications['enabled'] and not self.ranking['enabled']:
                raise ValueError('일반 클릭 버튼, 조건 규칙 또는 진행 보조 클릭을 하나 이상 켜세요.')
            config['dry'] = self.dry.get()
            if not self.confirm.get():
                raise ValueError('게임 모드 설정 확인을 체크하세요.')
            import json
            (ROOT/'last_settings.json').write_text(json.dumps({k: v for k, v in config.items() if k not in ('buttons','rules','progress','notifications','ranking')}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            messagebox.showerror('설정 확인', str(e))
            return
        self.stop.clear()
        self.start_button.configure(state='disabled')
        self.worker = threading.Thread(target=self.run, args=(config,), daemon=True)
        self.worker.start()

    def run(self, cfg):
        png, results, records, machine = None, {}, {}, None
        try:
            from vision import Matcher
            from registry import validate
            validate(cfg['buttons'])
            rules = cfg.get('rules', [])
            progress_config=cfg.get('progress',default_progress())
            notification_config=cfg.get('notifications',default_notifications())
            notifier=NotificationDetector(notification_config,cfg['buttons'])
            ranking_config=cfg.get('ranking',default_ranking())
            ranking=RankingMachine(ranking_config,cfg['buttons'],cfg.get('games',1))
            validate_rules(rules, cfg['buttons'])
            progress=ProgressClicker(progress_config,cfg['buttons'])
            referenced = {key for rule in rules if rule['enabled'] for key in
                          [rule['trigger'], rule['success']]+[s['button'] for s in rule['steps']] if key}
            if progress_config['enabled']:
                referenced.update(progress_config['watch'])
            if notification_config['enabled']:
                referenced.update(notification_config['watch'])
            if ranking_config['enabled']:
                referenced.update(ranking_config[key] for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','reward','reward_confirm','refresh_dialog','points_confirm','star_confirm') if ranking_config.get(key))
            records = {r['id']: r for r in cfg['buttons'] if r['enabled'] and
                       (r.get('kind', 'normal') == 'normal' or r['id'] in referenced)}
            if not records:
                raise ValueError('사용할 버튼이 없습니다.')
            color_keys={ranking_config[key] for key in ('series','disabled','refresh','points','paid','reward','points_confirm','star_confirm') if ranking_config.get(key)} if ranking_config['enabled'] else set()
            matchers = {key: Matcher(self.registry.resolve(r['template']), r['threshold'], r['roi'], **({'color_tolerance':25} if key in color_keys else {}))
                        for key, r in records.items()}
            expected = {key: tuple(r['size']) for key, r in records.items() if r.get('size')}
            adb = ADB(cfg['adb'], cfg['serial'])
            adb.connect()
            frames, interval = speed_settings(cfg.get('speed', DEFAULT_MODE))
            machine = WorkflowMachine(records, rules, time.monotonic(), detection_frames=frames)
            self.emit('시작 · ' + ('모의 실행' if cfg['dry'] else '실제 탭 전송'))
            self.emit('일반 버튼과 조건 규칙의 나타남 / 사라짐 / 최종 이미지 대기는 시간 제한 없이 계속합니다.')
            self.emit(f'반응속도 · 인식 {frames}회 / 추가 화면 대기 {interval:g}초 / 전환 확인 3회 유지')
            self.emit('일반 클릭 우선순위 · ' + ' → '.join(records[key]['name'] for key in machine.normal_ids))
            if progress_config['enabled']:
                self.emit(f'진행 보조 클릭 ON · 두 이미지 미인식 {float(progress_config["delay"]):g}초 후 / {float(progress_config["interval"]):g}초 간격 / 좌표={tuple(progress_config["point"])}')
            if ranking_config['enabled']:self.emit(f'랭킹 모드 · 목표 연속 경기 {ranking.target}회 · 연속 경기 우선 · 비활성 확인 시 무료/일반 포인트 갱신 사용 · 별 갱신 설정={ranking_config.get("allow_star",False)}')
            dimensions = None
            previous_state = None
            heartbeat = taps = 0
            while not self.stop.is_set():
                png = adb.screenshot()
                results = {name: matcher.find(png) for name, matcher in matchers.items()}
                size = next(iter(results.values()))[3]
                if (dimensions is not None and dimensions != size) or any(v != size for v in expected.values()):
                    raise RuntimeError('등록 이미지와 화면 해상도가 다릅니다. 현재 해상도로 다시 등록하세요.')
                dimensions = size
                progress.check_size(size)
                for record in records.values():
                    if record.get('click_mode') == 'fixed':
                        click_point(record,(0,0),size)
                if self.stop.is_set():
                    break
                now = time.monotonic()
                hits=notifier.observe({key:result[0] for key,result in results.items()})
                if hits:
                    names=', '.join(records[key]['name'] for key in hits)
                    if cfg['dry']:
                        self.emit(f'모의 휴대폰 알림 · {names} · 실제 전송 안 함')
                    else:
                        self.dispatch_notification(notification_config,notification_config['message']+'\n감지: '+names)
                    if notification_config['stop']:
                        self.emit('알림 이미지 감지 · 설정에 따라 클릭 전에 매크로 중지')
                        break
                if notification_config['enabled'] and notification_config['stop'] and any(results[key][0] for key in notification_config['watch']):
                    self.stop.wait(interval)
                    continue
                ranking_action=ranking.observe({key:result[0] for key,result in results.items()},now)
                while ranking.events:self.emit(ranking.events.pop(0))
                if ranking.finished:
                    self.emit('랭킹 목표 횟수 완료 · 다음 연속 경기 시작 전에 중지');break
                if ranking.hold:
                    if ranking_action:
                        if self.stop.is_set():break
                        _,matched_point,score,_=results[ranking_action]
                        point=click_point(records[ranking_action],matched_point,size)
                        if not cfg['dry']:adb.tap(point)
                        self.emit(f'{"모의 랭킹 탭" if cfg["dry"] else "랭킹 탭"} {records[ranking_action]["name"]} 좌표={point} 점수={score:.3f}')
                        if ranking_action==ranking_config['proceed']:
                            machine=WorkflowMachine(records,rules,now,detection_frames=frames)
                    if now-heartbeat>=5:
                        labels={'lobby':'무료/별 갱신 상태 확인','series':'연속 경기 대기','lineup':'라인업 경기 진행 대기','departing':'경기 화면 진입 대기','playing':'경기 진행','result_confirm':'결과 확인 버튼 대기','returning':'결과 확인 후 로비 복귀 대기','reward_confirm':'15회 보상 팝업 확인 대기','reward_return':'15회 보상 수령 후 로비 확인 대기','points_confirm':'즉시 갱신 포인트 확인 대기','star_confirm':'즉시 갱신 별 확인 대기','refresh_return':'즉시 갱신 팝업 종료 대기'}
                        scores=' / '.join(f'{records[key]["name"]}={results[key][2]:.3f}({"인식" if results[key][0] else "미인식"})' for key in referenced if key in results)
                        self.emit(f'랭킹 대기 · {labels[ranking.phase]} · 완료 {ranking.completed}/{ranking.target}회 · {scores}')
                        heartbeat=now
                    self.stop.wait(interval);continue
                action = machine.observe({n: r[0] for n, r in results.items()}, now)
                while machine.events:
                    self.emit(machine.events.pop(0))
                if machine.finished:
                    self.emit('규칙 완료 설정에 따라 매크로를 중지합니다.')
                    break
                if action:
                    if self.stop.is_set():
                        break
                    _, matched_point, score, _ = results[action]
                    point = click_point(records[action],matched_point,size)
                    if not cfg['dry']:
                        adb.tap(point)
                    taps += 1
                    label = records[action]['name']
                    mode='지정 좌표' if records[action].get('click_mode')=='fixed' else '이미지 중앙'
                    self.emit(f'{"모의 탭" if cfg["dry"] else "탭"} #{taps} {label} 좌표={point} 인식위치={matched_point} 방식={mode} 점수={score:.3f}')
                blocked=(action is not None or machine.active is not None or machine.queued is not None or
                         machine.normal.pending is not None or now<machine.normal.next_allowed or now<machine.next_allowed or
                         any(results[key][0] for key in machine.normal_ids))
                extra=progress.observe({key:result[0] for key,result in results.items()},now,blocked=blocked)
                if extra is not None:
                    if self.stop.is_set():break
                    if not cfg['dry']:adb.tap(extra)
                    self.emit(f'{"모의 보조 탭" if cfg["dry"] else "보조 탭"} #{progress.taps} 좌표={extra} · 지정한 두 이미지 모두 미인식')
                state = machine.status
                if previous_state != state or now-heartbeat >= 5:
                    scores = ' / '.join(f'{records[n]["name"]}={r[2]:.3f}(기준 {records[n]["threshold"]:.3f})' for n, r in results.items())
                    self.emit(('모의 실행 · ' if cfg['dry'] else '실제 실행 · ') + state + ' · ' + scores)
                    if machine.normal.pending and machine.active is None:
                        name=records[machine.normal.pending]['name']
                        self.emit(f'클릭 보류 · {name} 사라짐 확인 {machine.normal.absent}/3회. 아직 인식되면 다음 클릭을 보내지 않습니다.')
                    previous_state, heartbeat = state, now
                self.stop.wait(interval)
        except Exception as e:
            self.emit(f'안전 중지 · {e}')
            if isinstance(e, SafetyStop):
                if cfg['dry']:
                    self.emit('모의 실행은 클릭하지 않습니다. 모의 탭 후 게임 버튼을 직접 눌러 전환을 확인하세요.')
                else:
                    self.emit('게임에서 버튼이 실제로 눌렸는지 확인하세요. 누르지 않았다면 등록 이미지·검색 영역을 다시 확인하세요.')
                if png is not None and results:
                    try:
                        from diagnostics import write_stop_snapshot
                        path = write_stop_snapshot(ROOT, png, records, results,
                                                   machine.pending if machine else None, cfg['dry'])
                        self.emit(f'중지 직전 화면 저장 · {path}')
                        scores = ' / '.join(f'{records[n]["name"]}={r[2]:.3f} (기준 {records[n]["threshold"]:.3f})' for n, r in results.items())
                        self.emit('마지막 인식 · ' + scores)
                    except Exception as diagnostic_error:
                        self.emit(f'중지 화면 저장 실패 · {diagnostic_error}')
        finally:
            self.stop.set()
            self.emit('실행 종료')

    def poll(self):
        for _ in range(100):
            try:
                msg = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(msg, tuple):
                if msg[0]=='notification_test':
                    if not self.closing and msg[1].window.winfo_exists():msg[1].test_finished(msg[2])
                    continue
                if msg[0].startswith('update_'):
                    if not self.closing:
                        self.update_event(msg)
                    continue
                self.capturing = False
                if not self.closing:
                    try:
                        if msg[0] == 'capture':
                            msg[1](msg[2])
                            self.status.set('캡처 완료 · 열린 창에서 버튼 영역을 선택하세요.')
                        else:
                            messagebox.showerror('캡처 실패', msg[1])
                    except Exception as e:
                        messagebox.showerror('캡처 실패', str(e))
                continue
            self.status.set(msg)
            self.log.configure(state='normal')
            self.log.insert('end', time.strftime('%H:%M:%S') + ' ' + msg + '\n')
            if int(self.log.index('end-1c').split('.')[0]) > 1200:
                self.log.delete('1.0', '201.0')
            self.log.see('end')
            self.log.configure(state='disabled')
        running = self.worker and self.worker.is_alive()
        if not running and not self.updating:
            self.start_button.configure(state='normal')
        if self.closing and not running:
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def close(self):
        if self.updating:
            messagebox.showinfo('업데이트 중', '다운로드가 끝날 때까지 기다려 주세요. 실패 시 기존 프로그램을 계속 사용할 수 있습니다.')
            return
        self.closing = True
        self.stop.set()
        self.status.set('종료 대기 중 (진행 중인 ADB 호출 최대 5초)')


if __name__ == '__main__':
    root = tk.Tk()
    try:
        app = App(root)
    except Exception as e:
        messagebox.showerror('프로그램 시작 실패', f'설정 파일과 폴더 쓰기 권한을 확인하세요.\n{e}')
        root.destroy()
        raise SystemExit(1)
    if '--self-test' in sys.argv:
        from vision import Matcher
        from capture import editor, point_editor
        png = (ROOT/'assets'/'catcher.png').read_bytes()
        found, point, score, size = Matcher(ROOT/'assets'/'catcher.png').find(png)
        assert found
        editor(root, png, ROOT/'logs'/'unused.png', lambda *args: None)
        point_editor(root, png, lambda *args: None)
        app.manage()
        app.manager.edit(True)
        app.manage_rules()
        app.rule_manager.edit(True)
        app.manage_progress()
        app.manage_notifications()
        app.manage_ranking()
        root.update()
        (ROOT/'logs'/'selftest.json').write_text(json.dumps({
            'frozen': bool(getattr(sys, 'frozen', False)), 'template_score': score,
            'gui': 'ok', 'capture_editor': 'ok', 'point_editor': 'ok', 'button_manager': 'ok', 'rule_manager': 'ok', 'progress_manager': 'ok', 'notification_manager': 'ok', 'ranking_manager': 'ok'}), encoding='utf-8')
        root.destroy()
    else:
        root.mainloop()
