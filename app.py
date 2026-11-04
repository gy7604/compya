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
from league_count import LeagueCounter
from league_season import SeasonProgress,RoundReader
from league_timing import LeagueTiming,START_NAMES,duration_text
from league_rewards import LeagueRewards
from ball_guard import BallGuard
from ball_rest import BallRest, begin_ball_rest
from ball_balance import BallBalanceReader
from sponsor import Sponsor
from league_bonus import LeagueBonus
from collections import deque
from log_view import concise_status,important_message
from stall import StallWatch,TransitionWatch
from recovery import RecoveryStore,RecoveryMachine,default_recovery,validate_recovery,recovery_for_mode
from ranking import RankingStore, RankingMachine, default_ranking, validate_ranking
from notifications import NotificationStore, NotificationDetector, default_notifications, validate_notifications, send_notification
from progress import ProgressStore, ProgressClicker, default_progress, validate_progress
from ranking_schedule import ScheduleStore, ScheduledRunner
from interruptions import CoachAdvice, VideoSkipper, PopupCloser
from runtime_resources import ensure_runtime_resources

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


class App:
    def __init__(self, root):
        self.root = root
        resource_errors=ensure_runtime_resources(ROOT)
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
        self.schedule_store=ScheduleStore(ROOT)
        self.schedule=self.schedule_store.load()
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
        self.recovery_store=RecoveryStore(ROOT)
        self.recovery=self.recovery_store.load()
        self.recovery_manager=None
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
        ttk.Button(form,text='자동 재접속 · 광고 닫기 설정',command=self.manage_recovery).grid(row=8,column=0,columnspan=2,sticky='w',pady=5)
        self.capturing = False
        self.dry = tk.BooleanVar(value=True)
        self.confirm = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text='모의 실행 (탭 전송 안 함)', variable=self.dry).grid(row=5, column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(form, text='게임에서 타격 OFF · 투구 ON · 풀 플레이를 직접 설정했습니다.', variable=self.confirm).grid(row=6, column=0, columnspan=3, sticky='w')
        ttk.Label(form,text='랭킹: 연속 경기 결과 화면을 1회로 집계 · 시작마다 0회로 초기화').grid(row=7,column=0,columnspan=2,sticky='w')
        ttk.Button(form,text='랭킹 챌린지 설정',command=self.manage_ranking).grid(row=7,column=2,padx=8)
        ttk.Button(form,text='평일/주말 랭킹 일정',command=self.manage_schedule).grid(row=8,column=1,sticky='w')
        for row,key,label,preferred in [(9,'league_start','리그 집계 · 경기 중 이미지','포수리드'),(10,'league_result','리그 집계 · 경기 결과 이미지','경기결과 다음')]:
            initial=saved.get(key,next((r['name'] for r in self.records if r['name']==preferred),'미사용'))
            self.fields[key]=tk.StringVar(value=initial)
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w')
            combo=ttk.Combobox(form,textvariable=self.fields[key],state='readonly',width=38)
            combo.configure(values=['미사용']+[r['name'] for r in self.records],postcommand=lambda c=combo:c.configure(values=['미사용']+[r['name'] for r in self.records]))
            combo.grid(row=row,column=1,sticky='w')
        self.league_count_text=tk.StringVar(value='이번 실행 · 리그 완료 0경기 (시작마다 초기화)')
        ttk.Label(root,textvariable=self.league_count_text).pack(anchor='w',padx=12,pady=3)
        self.league_season_text=tk.StringVar(value=SeasonProgress(ROOT).text())
        ttk.Label(root,textvariable=self.league_season_text).pack(anchor='w',padx=12,pady=3)
        self.league_time_text=tk.StringVar(value=LeagueTiming(ROOT).text())
        ttk.Label(root,textvariable=self.league_time_text).pack(anchor='w',padx=12,pady=3)
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
        ttk.Label(buttons, textvariable=self.status,wraplength=650).pack(side='left')
        log_bar=ttk.Frame(root)
        log_bar.pack(fill='x',padx=12,pady=(8,0))
        ttk.Label(log_bar,text='주요 기록 · 상세 내용은 logs/macro.log에 자동 저장').pack(side='left')
        self.detailed_log=tk.BooleanVar(value=False)
        ttk.Checkbutton(log_bar,text='상세 로그 보기',variable=self.detailed_log,command=self.render_log).pack(side='right')
        self.log_history=deque(maxlen=300)
        self.important_history=deque(maxlen=300)
        log_frame=ttk.Frame(root)
        log_frame.pack(fill='both',expand=True,padx=12,pady=(4,12))
        self.log = tk.Text(log_frame, height=12, state='disabled',wrap='word')
        scrollbar=ttk.Scrollbar(log_frame,orient='vertical',command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y')
        self.log.pack(side='left',fill='both',expand=True)
        self.log.tag_configure('error',foreground='#b42318')
        (ROOT/'logs').mkdir(exist_ok=True)
        self.logger = logging.getLogger('macro')
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(ROOT/'logs'/'macro.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(handler)
        for name in resource_errors:self.logger.warning('업데이트 보조 파일 준비 실패 · %s',name)
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
        if (self.recovery_manager and self.recovery_manager.window.winfo_exists()) or (self.ranking_manager and self.ranking_manager.window.winfo_exists()) or (self.notification_manager and self.notification_manager.window.winfo_exists()) or self.capturing or (self.worker and self.worker.is_alive()) or (self.manager and self.manager.window.winfo_exists()) or (self.rule_manager and self.rule_manager.window.winfo_exists()) or (self.progress_manager and self.progress_manager.window.winfo_exists()):
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
            self.recovery_store.save(self.recovery)
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

    def manage_recovery(self):
        if self.updating or self.capturing or (self.worker and self.worker.is_alive()):
            messagebox.showinfo('재접속 설정','실행을 중지한 뒤 설정하세요.');return
        from recovery_ui import RecoveryManager
        if self.recovery_manager and self.recovery_manager.window.winfo_exists():
            self.recovery_manager.window.lift();return
        self.recovery_manager=RecoveryManager(self)

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
            config['recovery']=recovery_for_mode(copy.deepcopy(self.recovery),self.ranking['enabled'])
            validate_recovery(config['recovery'],self.records)
            if self.recovery['enabled'] and not config['recovery']['enabled']:
                self.emit('현재 모드와 복귀 대상이 달라 자동 재접속은 사용하지 않습니다. 일반 진행은 계속합니다.')
            config['schedule']=dict(self.schedule)
            config['coach_advice']=True
            if self.schedule['enabled']:
                from ranking_schedule import validate as validate_schedule
                validate_schedule(self.schedule,self.records,self.ranking,self.recovery)
            else:validate_ranking(self.ranking,self.records)
            validate_notifications(self.notifications,self.records)
            validate_rules(self.rules, self.records)
            validate_progress(self.progress,self.records)
            if not any(r['enabled'] and r.get('kind', 'normal') == 'normal' for r in self.records) and not any(r['enabled'] for r in self.rules) and not self.progress['enabled'] and not self.notifications['enabled'] and not self.ranking['enabled']:
                raise ValueError('일반 클릭 버튼, 조건 규칙 또는 진행 보조 클릭을 하나 이상 켜세요.')
            if not self.ranking['enabled']:
                chosen=[config['league_start'],config['league_result']]
                if chosen[0]!='미사용' and chosen[1]!='미사용':
                    available={r['name'] for r in self.records if r['enabled'] and r['template']}
                    if any(name not in available for name in chosen) or chosen[0]==chosen[1]:
                        raise ValueError('경기 집계에는 사용 중인 서로 다른 경기 중/결과 이미지를 선택하세요. 미사용으로 두면 집계 없이 실행합니다.')
            config['dry'] = self.dry.get()
            if not self.confirm.get():
                raise ValueError('게임 모드 설정 확인을 체크하세요.')
            import json
            (ROOT/'last_settings.json').write_text(json.dumps({k: v for k, v in config.items() if k not in ('buttons','rules','progress','notifications','ranking','recovery')}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            messagebox.showerror('설정 확인', str(e))
            return
        self.stop.clear()
        self.start_button.configure(state='disabled')
        self.worker = threading.Thread(target=self.run, args=(config,), daemon=True)
        self.worker.start()

    def run(self, cfg):
        if not cfg.get('schedule',{}).get('enabled'):
            return self.run_session(cfg)
        try:
            ScheduledRunner(self,cfg,ROOT).run()
        except Exception as error:
            self.emit(f'일정 실행 중지 · {error}')
        finally:
            self.stop.set()
            self.emit('일정 실행 종료')

    def manage_schedule(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('실행 중','중지 후 일정 설정을 변경하세요.');return
        from ranking_schedule_ui import ScheduleManager
        ScheduleManager(self)

    def run_session(self, cfg):
        png, results, records, machine = None, {}, {}, None
        previous_png=None
        last_click=None
        recovery=ranking=None
        try:
            from vision import Matcher,PriceMatcher,OverlayMatcher
            from registry import validate
            validate(cfg['buttons'])
            rules = cfg.get('rules', [])
            progress_config=cfg.get('progress',default_progress())
            notification_config=cfg.get('notifications',default_notifications())
            notifier=NotificationDetector(notification_config,cfg['buttons'])
            ranking_config=dict(cfg.get('ranking',default_ranking()))
            ranking_config['all_attempts']=bool(cfg.get('_daily'))
            if cfg.get('_daily'):ranking_config['resume_active_batch']=cfg['_daily'].entry.get('active_batch')
            ranking=RankingMachine(ranking_config,cfg['buttons'],cfg.get('games',1))
            from ranking_summary import RankingResultReader
            result_reader=RankingResultReader(ROOT) if cfg.get('_daily') and cfg['schedule'].get('completion_notice') else None
            if cfg.get('_daily'):
                ranking.completed=cfg['_daily'].entry['completed']
                if ranking.reward and cfg['_daily'].entry.get('reward_pending'):
                    ranking.phase='reward_confirm'
                    ranking.resuming_reward=True
                    self.emit('랭킹 보상 수령 이어받기 · 획득 팝업 확인 후 로비 복귀')
            recovery_config=recovery_for_mode(cfg.get('recovery',default_recovery()),ranking_config['enabled'])
            recovery=RecoveryMachine(recovery_config,cfg['buttons'])
            popup_closer=PopupCloser(recovery_config['popups'] if recovery_config['enabled'] else [])
            if cfg.get('_activate_route'):recovery.active=True
            coach=CoachAdvice()
            video_keys=[r['id'] for r in cfg['buttons'] if r['enabled'] and r.get('video_skip') is True]
            video_ends=[r['id'] for r in cfg['buttons'] if r['enabled'] and r.get('video_intro_end') is True]
            video=VideoSkipper(video_keys,[r['id'] for r in cfg['buttons'] if r['id'] in video_keys and r.get('video_intro') is True],video_ends)
            coach_matcher=Matcher(ROOT/'assets'/'coach_advice.png',.94,[265,120,450,215]) if cfg.get('coach_advice') and (ROOT/'assets'/'coach_advice.png').exists() else None
            watchdog=StallWatch(recovery_config.get('stall_seconds',180))
            transition_watch=TransitionWatch()
            watch_enabled=recovery_config['enabled'] and recovery_config.get('stall_enabled',False)
            last_process_poll=float('-inf')
            validate_rules(rules, cfg['buttons'])
            progress=ProgressClicker(progress_config,cfg['buttons'])
            count_ids={r['name']:r['id'] for r in cfg['buttons'] if r['enabled'] and r['template']}
            counter=LeagueCounter(count_ids.get(cfg.get('league_start',''),''),count_ids.get(cfg.get('league_result',''),''),frames=speed_settings(cfg.get('speed',DEFAULT_MODE))[0])
            count_enabled=not ranking_config['enabled'] and bool(counter.start and counter.result)
            if count_enabled:
                # Stop introductory taps as soon as gameplay/results are visible,
                # even if a short PLAY BALL overlay escaped capture.
                recovery_ends=([recovery_config['goal']]+recovery_config['steps'][-1:]) if recovery_config['enabled'] else []
                video_ends=list(dict.fromkeys(video_ends+[counter.start,counter.result]+recovery_ends))
                video.end_keys=video_ends
            rewards=LeagueRewards(ROOT,enabled=not ranking_config['enabled'],dry=cfg['dry'])
            balls=BallGuard(ROOT)
            rest=BallRest(ROOT,dry=cfg['dry'])
            balance=BallBalanceReader(ROOT)
            sponsor=Sponsor(ROOT,enabled=not ranking_config['enabled'],dry=cfg['dry'])
            bonus=LeagueBonus(ROOT,enabled=not ranking_config['enabled'],dry=cfg['dry'])
            season=SeasonProgress(ROOT,dry=cfg['dry'])
            timing=LeagueTiming(ROOT,dry=cfg['dry'])
            round_reader=RoundReader(ROOT) if count_enabled else None
            lobby_since=None;lobby_ready=False;last_time_text=None
            def season_event():
                if hasattr(self,'events'):self.events.put(('league_season',('모의 · ' if cfg['dry'] else '')+season.text(ranking_config['enabled'])))
            def time_event(now=None):
                nonlocal last_time_text
                label=('모의 · ' if cfg['dry'] else '')+timing.text(now)
                if ranking_config['enabled']:label+=' · 랭킹 진행 중'
                if label!=last_time_text and hasattr(self,'events'):self.events.put(('league_time',label))
                last_time_text=label
            def league_tap(key,now):
                if not count_enabled:return
                record=records[key]
                if record.get('season_start') or record['name']=='새로운리그의시작':
                    if season.new_season():season_event()
                    timing.cancel();time_event(now)
                if record.get('league_match_start') or record['name'] in START_NAMES:
                    timing.start(now);time_event(now)
            season_event()
            time_event()
            if hasattr(self,'events'):self.events.put(('league_count',0,cfg['dry'],count_enabled))
            referenced = {key for rule in rules if rule['enabled'] for key in
                          [rule['trigger'], rule['success']]+[s['button'] for s in rule['steps']] if key}
            if count_enabled:referenced.update([counter.start,counter.result])
            if progress_config['enabled']:
                referenced.update(progress_config['watch'])
            if notification_config['enabled']:
                referenced.update(notification_config['watch'])
            if ranking_config['enabled']:
                referenced.update(ranking_config[key] for key in ('lobby','refresh','points','paid','series','disabled','proceed','result','confirm','reward','reward_confirm','refresh_dialog','points_confirm','star_confirm','promotion') if ranking_config.get(key))
                if cfg.get('_daily'):
                    referenced.update(cfg['schedule'][key] for key in ('points_price','star_price','exhausted') if cfg['schedule'].get(key))
            if recovery_config['enabled']:
                referenced.update([recovery_config['goal']]+recovery_config['steps']+recovery_config['popups'])
                if recovery_config.get('target')=='league':referenced.update(recovery_config.get('checkpoints',[]))
            referenced.update(video_keys)
            referenced.update(video_ends)
            records = {r['id']: r for r in cfg['buttons'] if r['enabled'] and
                       (r.get('kind', 'normal') == 'normal' or r['id'] in referenced)}
            if not records:
                raise ValueError('사용할 버튼이 없습니다.')
            color_keys={ranking_config[key] for key in ('series','disabled','refresh','points','paid','reward','points_confirm','star_confirm') if ranking_config.get(key)} if ranking_config['enabled'] else set()
            matchers = {key: Matcher(self.registry.resolve(r['template']), r['threshold'], r['roi'], **({'color_tolerance':25} if key in color_keys else {}))
                        for key, r in records.items()}
            for key in video_keys:
                r=records[key]
                if r.get('video_text_mask') is True:matchers[key]=OverlayMatcher(self.registry.resolve(r['template']),r['threshold'],r['roi'])
            if cfg.get('_daily'):
                for field in ('points_price','star_price'):
                    key=cfg['schedule'].get(field)
                    if key:
                        r=records[key]
                        matchers[key]=PriceMatcher(self.registry.resolve(r['template']),max(.94,r['threshold']),r['roi'])
            expected = {key: tuple(r['size']) for key, r in records.items() if r.get('size')}
            adb = ADB(cfg['adb'], cfg['serial'])
            player_restart=None
            if watch_enabled and recovery_config.get('restart_player',True):
                from player_restart import PlayerRestart
                player_restart=PlayerRestart(recovery_config.get('player_console',''),recovery_config.get('player_index',0),adb,self.stop)
            frames, interval = speed_settings(cfg.get('speed', DEFAULT_MODE))
            machine = WorkflowMachine(records, rules, time.monotonic(), detection_frames=frames)
            self.emit('시작 · ' + ('모의 실행' if cfg['dry'] else '실제 탭 전송'))
            self.emit('일반 경기 버튼 대기는 유지 · 클릭 후 전환/규칙 진행은 최소 180초, 자동 복귀는 최소 600초 안에 확인되지 않으면 동작을 중단합니다.')
            self.emit(f'반응속도 · 인식 {frames}회 / 추가 화면 대기 {interval:g}초 / 규칙 전환 확인 3회 / 일반 버튼 사라짐 확인은 버튼별 설정')
            self.emit('일반 클릭 우선순위 · ' + ' → '.join(records[key]['name'] for key in machine.normal_ids))
            if progress_config['enabled']:
                self.emit(f'진행 보조 클릭 ON · 두 이미지 미인식 {float(progress_config["delay"]):g}초 후 / {float(progress_config["interval"]):g}초 간격 / 좌표={tuple(progress_config["point"])}')
            if ranking_config['enabled']:
                goal='오늘 허용된 전체 횟수' if cfg.get('_daily') else f'목표 연속 경기 {ranking.target}회'
                self.emit(f'랭킹 모드 · {goal} · 연속 경기 우선 · 비활성 확인 시 무료/P 갱신 사용 · 별 갱신 설정={ranking_config.get("allow_star",False)}')
            dimensions = None
            previous_state = None
            heartbeat = taps = 0
            need_connect=True
            adb_failures=0

            def reboot_player(reason):
                if self.stop.is_set():return False
                if cfg['dry']:
                    raise SafetyStop('모의 복구 감지 · '+reason+' · LDPlayer 재시작/게임 실행 안 함')
                timing.cancel();time_event()
                while not self.stop.is_set():
                    if not recovery.reserve_player_restart(time.monotonic()):return False
                    while recovery.events:self.emit(recovery.events.pop(0))
                    self.emit(f'자동 복구 · {reason} · LDPlayer {player_restart.index}번 재시작 · 부팅/ADB 연결 대기')
                    try:
                        if not player_restart.restart():
                            self.stop.set();return False
                        if self.stop.is_set():return False
                        adb.launch_app(recovery_config['package'])
                    except RuntimeError as restart_error:
                        self.emit(f'플레이어 복구 실패 · {recovery.launches}/{recovery_config["max_launches"]}회 · {restart_error}')
                        if recovery.launches>=recovery_config['max_launches']:
                            raise SafetyStop('자동 복구 한도 도달 · 플레이어 재시작/게임 실행 실패') from restart_error
                        if self.stop.wait(30):return False
                        continue
                    watchdog.reset()
                    self.emit('자동 복구 · LDPlayer 부팅 완료 · 게임 실행 · 목표 모드 복귀 대기')
                    return True
                return False

            def recover_transition(error):
                self.emit(f'화면 진행 복구 요청 · {error}')
                if png is not None:
                    try:
                        from diagnostics import write_stop_snapshot
                        write_stop_snapshot(ROOT,png,records,results,machine.pending,cfg['dry'],
                                            context=dict(error=str(error),recovery_launches=recovery.launches,
                                                         workflow=machine.status),previous_png=previous_png)
                        self.emit('전환 실패 화면 저장 · logs/last_stop.png · 자동 복구 계속')
                    except Exception as diagnostic_error:
                        self.emit(f'전환 진단 저장 실패 · {diagnostic_error}')
                if cfg['dry'] or not watch_enabled:raise error
                if player_restart:
                    reboot_player('화면 전환 미확인')
                elif recovery.restart_stalled(time.monotonic()):
                    timing.cancel();time_event()
                    adb.stop_app(recovery_config['package'])
                    if not self.stop.is_set():adb.launch_app(recovery_config['package'])
                watchdog.reset()

            def reset_maintenance():
                nonlocal machine,progress,recovery,transition_watch,last_process_poll
                machine=WorkflowMachine(records,rules,time.monotonic(),detection_frames=frames)
                progress=ProgressClicker(progress_config,cfg['buttons'])
                launches,last_launch=recovery.launches,recovery.last_launch
                recovery=RecoveryMachine(recovery_config,cfg['buttons'])
                recovery.launches,recovery.last_launch=launches,last_launch
                transition_watch=TransitionWatch();watchdog.reset();last_process_poll=float('-inf')

            while not self.stop.is_set():
                if not ranking_config['enabled']:
                    rest.tick()
                    while rest.events:
                        rest_message=rest.events.pop(0);self.emit(rest_message)
                        if hasattr(self,'events'):self.events.put(('league_time',rest_message))
                try:
                    if need_connect:
                        if player_restart:player_restart.connect_selected()
                        else:adb.connect()
                        need_connect=False
                    if recovery_config['enabled'] and not ((rest.active or (sponsor.active and not sponsor.recovering)) and not ranking_config['enabled']):
                        poll_now=time.monotonic()
                        if poll_now-last_process_poll>=2:
                            last_process_poll=poll_now
                            launch=recovery.poll(adb.app_running(recovery_config['package']),poll_now)
                            while recovery.events:self.emit(recovery.events.pop(0))
                            if launch:
                                if self.stop.is_set():break
                                timing.cancel();time_event()
                                if not cfg['dry']:adb.launch_app(recovery_config['package'])
                                self.emit('모의 게임 재실행 · 실제 실행 안 함' if cfg['dry'] else '게임 실행 요청 전송 · 접속 화면 대기')
                        if not recovery.running:
                            self.stop.wait(.2);continue
                    next_png = adb.screenshot()
                    adb_failures=0
                except SafetyStop as error:
                    if rest.active and not ranking_config['enabled']:
                        self.stop.wait(2);continue
                    if watch_enabled and player_restart and not cfg['dry']:
                        reboot_player(str(error));self.stop.wait(1);continue
                    raise
                except RuntimeError as connection_error:
                    if rest.active and not ranking_config['enabled']:
                        need_connect=True;self.stop.wait(2);continue
                    if not player_restart:raise
                    adb_failures+=1;need_connect=True
                    self.emit(f'ADB 연결/화면 수신 실패 · {adb_failures}/3회 · {connection_error}')
                    if self.stop.is_set():break
                    if adb_failures>=3:
                        if reboot_player('ADB 연속 실패'):
                            adb_failures=0;need_connect=False;last_process_poll=float('-inf')
                    self.stop.wait(2);continue
                previous_png,png=png,next_png
                maintenance_now=time.monotonic() if isinstance(png,bytes) else 0
                popup_results={key:matchers[key].find(png) for key in popup_closer.keys}
                popup_action=popup_closer.observe({key:value[0] for key,value in popup_results.items()},maintenance_now)
                while popup_closer.events:self.emit(popup_closer.events.pop(0))
                if popup_closer.resumed:
                    machine.league_result(maintenance_now)
                    transition_watch=TransitionWatch();watchdog.reset()
                    video=VideoSkipper(video_keys,[r['id'] for r in cfg['buttons'] if r['id'] in video_keys and r.get('video_intro') is True],video_ends)
                if popup_closer.hold:
                    watchdog.reset();transition_watch=TransitionWatch()
                    if popup_action and not self.stop.is_set():
                        matched,center,score,popup_size=popup_results[popup_action]
                        if expected.get(popup_action,popup_size)!=popup_size:raise SafetyStop('공지/광고 등록 해상도와 현재 화면이 다릅니다.')
                        point=click_point(records[popup_action],center,popup_size)
                        if not cfg['dry']:adb.tap(point)
                        last_click=dict(button=popup_action,point=point,time=maintenance_now,dry=cfg['dry'])
                        self.emit(f'{"모의 " if cfg["dry"] else ""}공지/광고 우선 닫기 · {records[popup_action]["name"]} · 점수={score:.3f}')
                    self.stop.wait(.2);continue
                if not ranking_config['enabled']:
                    if balls.observe(png,maintenance_now,self.stop):
                        if not rest.active:
                            begin_ball_rest(self,cfg,balls,rest,png,ROOT)
                            timing.cancel();time_event()
                    ball_point=rest.close_point(balls.layout,maintenance_now) if rest.active else None
                    if ball_point:
                        if not cfg['dry'] and not self.stop.is_set():adb.tap(ball_point)
                        watchdog.reset();self.stop.wait(.2);continue
                sponsor_was_active=sponsor.active
                sponsor_point=sponsor.observe(png,maintenance_now,self.stop)
                if sponsor.active and not sponsor_was_active:timing.cancel();time_event()
                while sponsor.events:self.emit(sponsor.events.pop(0))
                if sponsor.finished:reset_maintenance()
                if sponsor.hold:
                    watchdog.reset();transition_watch=TransitionWatch()
                    if sponsor_point and not cfg['dry'] and not self.stop.is_set():adb.tap(sponsor_point)
                    self.stop.wait(.2);continue
                if rest.active and not ranking_config['enabled']:
                    if cfg.get('_handoff') and cfg['_handoff']():
                        cfg['_yielded']=True;self.emit('볼 충전 대기 중 랭킹 시간 도달 · 랭킹으로 전환');break
                    if rest.due() and balance.visible(png):
                        stock=balance.read(png,self.stop)
                        if self.stop.is_set():break
                        resumed=rest.submit(stock)
                        try:
                            folder=ROOT/'logs';folder.mkdir(exist_ok=True)
                            (folder/('ball_balance_confirmed.png' if resumed else 'ball_balance_last.png')).write_bytes(png)
                        except OSError:pass
                        if resumed:reset_maintenance();time_event();continue
                    watchdog.reset();transition_watch=TransitionWatch()
                    self.stop.wait(.2 if rest.candidate is not None else 1);continue
                if balls.hold and not ranking_config['enabled']:
                    watchdog.reset();transition_watch=TransitionWatch()
                    self.stop.wait(.2);continue
                bonus_point=bonus.observe(png,maintenance_now,self.stop)
                while bonus.events:self.emit(bonus.events.pop(0))
                if bonus.hold:
                    watchdog.reset();transition_watch=TransitionWatch()
                    if bonus_point and not cfg['dry'] and not self.stop.is_set():adb.tap(bonus_point)
                    self.stop.wait(.2);continue
                frame_now=time.monotonic() if rewards.enabled or count_enabled else None
                if count_enabled:time_event(frame_now)
                reward_point=rewards.observe(png,frame_now,self.stop) if rewards.enabled else None
                while rewards.events:self.emit(rewards.events.pop(0))
                if rewards.hold:
                    watchdog.reset();transition_watch=TransitionWatch()
                    if reward_point and not cfg['dry'] and not self.stop.is_set():adb.tap(reward_point)
                    if reward_point:self.emit(f'{"모의 " if cfg["dry"] else ""}시즌 보상 탭 · 좌표={reward_point}')
                    self.stop.wait(.2);continue
                if watch_enabled:
                    # A cold emulator/game launch can legitimately stay black
                    # longer than the user's in-game freeze threshold.
                    watchdog.seconds=max(180,recovery_config.get('stall_seconds',180)) if recovery.active else recovery_config.get('stall_seconds',180)
                    if not recovery.running:
                        watchdog.reset()
                    elif watchdog.observe(png,time.monotonic()):
                        self.emit(f'화면 멈춤 의심 · {watchdog.seconds}초 이상 유의미한 화면 변화·진행 없음')
                        diagnostic=ROOT/'logs'/'last_stall.png'
                        diagnostic.parent.mkdir(exist_ok=True)
                        diagnostic.write_bytes(png)
                        self.emit(f'멈춤 감지 화면 저장 · {diagnostic}')
                        if self.stop.is_set():break
                        if cfg['dry']:
                            self.emit('모의 멈춤 복구 · LDPlayer/게임 종료 및 재실행 안 함')
                        elif player_restart:
                            reboot_player('복귀 중 화면 정지' if recovery.active else '경기 화면 정지')
                            last_process_poll=float('-inf')
                        elif recovery.restart_stalled(time.monotonic()):
                            if self.stop.is_set():break
                            timing.cancel();time_event()
                            adb.stop_app(recovery_config['package'])
                            if self.stop.is_set():break
                            adb.launch_app(recovery_config['package'])
                            self.emit('멈춤 복구 · 게임 실행 · 등록 경로로 복귀 대기')
                        watchdog.reset()
                        self.stop.wait(max(.1,interval));continue
                if recovery_config['enabled'] and recovery.active:
                    import cv2,numpy as np
                    recovery_screen=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_GRAYSCALE)
                    if recovery_screen is None:raise RuntimeError('ADB 화면 PNG 해독 실패')
                    recovery_size=(recovery_screen.shape[1],recovery_screen.shape[0])
                    if any(v!=recovery_size for v in expected.values()):
                        orientation_now=time.monotonic()
                        if orientation_now-heartbeat>=5:
                            self.emit(f'재접속 화면 해상도 전환 대기 · 현재 {recovery_size}')
                            heartbeat=orientation_now
                        self.stop.wait(.2);continue
                if round_reader:
                    round_now=time.monotonic()
                    round_=round_reader.observe(png,round_now,self.stop)
                    if round_ is not None and season.lobby(round_):season_event()
                    if not round_reader.visible:
                        lobby_since=None;lobby_ready=False
                    else:
                        if lobby_since is None:lobby_since=round_now
                        if round_ is not None and season.frames>=2:lobby_ready=True
                        # Give two separate lobby frames a chance before PLAY BALL.
                        # A failed status read must never block gameplay indefinitely.
                        if not lobby_ready and round_now-lobby_since<4:
                            self.stop.wait(.1);continue
                video_results={key:matchers[key].find(png) for key in video_keys+video_ends}
                if video_results:
                    video_size=next(iter(video_results.values()))[3]
                    if (dimensions is None or dimensions==video_size) and all(v==video_size for v in expected.values()):
                        video_now=time.monotonic()
                        video_action=video.observe({key:result[0] for key,result in video_results.items()},video_now)
                        if video_action:
                            _,matched_point,score,_=video_results[video_action]
                            point=click_point(records[video_action],matched_point,video_size)
                            if not cfg['dry'] and not self.stop.is_set():adb.tap(point)
                            label=('모의 영상 스킵' if cfg['dry'] else '영상 스킵')+(' · 소개 이어 넘기기' if not video_results[video_action][0] else '')
                            self.emit(f'{label} · {records[video_action]["name"]} · 좌표={point} 점수={score:.3f}')
                        if video.resumed:
                            machine=WorkflowMachine(records,rules,video_now,detection_frames=frames)
                            transition_watch=TransitionWatch()
                        if video.hold:
                            watchdog.reset();self.stop.wait(.1);continue
                results = dict(video_results)
                results.update(popup_results)
                results.update({name: matcher.find(png) for name, matcher in matchers.items() if name not in results})
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
                if coach_matcher:
                    present,_,_,coach_size=coach_matcher.find(png)
                    if coach_size!=(720,1280):present=False
                    try:cancel_advice=coach.observe(present,now)
                    except SafetyStop as error:
                        recover_transition(error);self.stop.wait(1);continue
                    if cancel_advice:
                        if not cfg['dry'] and not self.stop.is_set():adb.tap((454,292))
                        self.emit('코치의 조언 · 투수 교체 안내 취소 · 경기 계속')
                    if coach.resumed:
                        machine=WorkflowMachine(records,rules,now,detection_frames=frames)
                        transition_watch=TransitionWatch()
                        self.emit('코치 안내 닫힘 확인 · 경기 버튼 다시 인식')
                    if coach.hold:
                        watchdog.reset();self.stop.wait(interval);continue
                try:recovery_action=recovery.observe({key:result[0] for key,result in results.items()},now)
                except SafetyStop as error:
                    recover_transition(error);self.stop.wait(1);continue
                while recovery.events:self.emit(recovery.events.pop(0))
                if recovery.restored:
                    transition_watch=TransitionWatch()
                    completed=ranking.completed;target_reached=ranking.target_reached;completion_reason=ranking.completion_reason
                    if cfg.get('_daily'):ranking_config['resume_active_batch']=cfg['_daily'].entry.get('active_batch')
                    ranking=RankingMachine(ranking_config,cfg['buttons'],cfg.get('games',1))
                    ranking.completed=completed;ranking.target_reached=target_reached
                    ranking.completion_reason=completion_reason
                    if ranking.reward and cfg.get('_daily') and cfg['_daily'].entry.get('reward_pending'):
                        ranking.phase='reward_confirm';ranking.resuming_reward=True
                    machine=WorkflowMachine(records,rules,now,detection_frames=frames)
                    progress=ProgressClicker(progress_config,cfg['buttons'])
                    if ranking_config['enabled']:
                        progress_label=f'오늘 완료 {completed}회' if cfg.get('_daily') else f'완료 {completed}/{ranking.target}회'
                        self.emit(f'재접속 복구 완료 · 연속 경기 {progress_label} 유지')
                    else:
                        self.emit(f'리그 복귀 완료 · 이번 실행 완료 {counter.completed}경기 유지 · 경기 진행 재개')
                if recovery.hold:
                    if recovery_action:
                        if self.stop.is_set():break
                        _,matched_point,score,_=results[recovery_action]
                        point=click_point(records[recovery_action],matched_point,size)
                        if not cfg['dry']:adb.tap(point)
                        league_tap(recovery_action,now)
                        last_click=dict(button=recovery_action,point=point,time=now,dry=cfg['dry'])
                        self.emit(f'{"모의 복구 탭" if cfg["dry"] else "복구 탭"} {records[recovery_action]["name"]} 좌표={point} 점수={score:.3f}')
                    if now-heartbeat>=5:
                        recovery_keys=[recovery_config['goal']]+recovery_config['steps']+recovery_config['popups']
                        recovery_scores=' / '.join(f'{records[key]["name"]}={results[key][2]:.3f}({"인식" if results[key][0] else "미인식"})' for key in recovery_keys)
                        self.emit(f'재접속 대기 · 단계 {min(recovery.index+1,len(recovery_config["steps"]))}/{len(recovery_config["steps"])} · {recovery_scores}')
                        heartbeat=now
                    self.stop.wait(max(.1,interval));continue
                if count_enabled and counter.observe({key:result[0] for key,result in results.items()}):
                    machine.league_result(now)
                    transition_watch=TransitionWatch()
                    watchdog.reset()
                    if season.result():season_event()
                    elapsed=timing.finish(now)
                    time_event(now)
                    self.events.put(('league_count',counter.completed,cfg['dry'],True))
                    self.emit(f'{"모의 집계" if cfg["dry"] else "리그 경기 완료"} · 이번 실행 {counter.completed}경기')
                    if elapsed:self.emit(f'리그 경기 시간 · {duration_text(elapsed[0])}'+(' · 경기 도중 시작하여 평균 제외' if elapsed[1] else ' · 경기 시작부터 결과 확인까지'))
                if count_enabled and counter.armed and results[counter.start][0] and not results[counter.result][0]:
                    if season.playing():season_event()
                    timing.start(now,partial=True);time_event(now)
                if (count_enabled and counter.result_frames>=counter.frames
                        and cfg.get('_handoff') and cfg['_handoff']()):
                    cfg['_yielded']=True
                    self.emit('리그 결과 확인 · 다음 경기 시작 전 랭킹으로 전환');break
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
                if cfg.get('_daily'):
                    from ranking_schedule import guard_action
                    daily=cfg['_daily']
                    currency=next((kind for field,kind in [('points_confirm','points'),('star_confirm','star')]
                                   if ranking_action and ranking_action==ranking_config.get(field)),None)
                    if currency and cfg['schedule'].get('ocr_prices',False):
                        from price_ocr import PriceReader
                        other=ranking_config.get('star_confirm' if currency=='points' else 'points_confirm')
                        receipt=PriceReader(ROOT/'PriceOCR.exe').verify(
                            adb,self.stop,lambda:daily.allowed(currency),ranking_action,
                            ranking_config['refresh_dialog'],other,matchers,records[ranking_action],size,with_count=True)
                        if receipt is None:break
                        amount,remaining,total=receipt
                        if remaining==0:
                            if daily.entry['pending']:raise SafetyStop('이전 구매 결과 미확인 · 추가 구매 없이 확인 필요')
                            daily.entry.update(paid_remaining=0,paid_total=total);daily.save()
                            cfg['_ranking_done']='게임의 추가 갱신 횟수 소진';break
                        daily.ocr_payment=(ranking_action,amount,time.monotonic(),remaining,total)
                        self.emit(f'랭킹 구매 금액 확인 · {"P포인트" if currency=="points" else "별"} {amount:,} · 남은 {remaining}/{total}회 · 3회 일치')
                    daily.price_frames=getattr(daily,'price_frames',{})
                    for key in ('points_price','star_price'):
                        price=cfg['schedule'].get(key)
                        if price:daily.price_frames[price]=daily.price_frames.get(price,0)+1 if results.get(price,(False,))[0] else 0
                    ranking_action=guard_action(daily,ranking,ranking_action,results,cfg['schedule'])
                    if result_reader and ranking_action==ranking_config['confirm'] and daily.entry.get('result_pending'):
                        batch=daily.entry['result_pending']
                        record=result_reader.verify(png,adb,self.stop,matchers[ranking_config['result']],matchers[ranking_config['confirm']])
                        if self.stop.is_set():break
                        daily.result_record(batch,record)
                        if record is None:
                            result_reader.retain_unreadable(png,daily.day,batch)
                            self.emit('랭킹 승무패 판독 불확실 · 결과 화면을 보존하고 전적 미집계로 표시 · 경기 계속')
                        else:self.emit(f'랭킹 결과 기록 · {sum(record)}경기 · {record[0]}승 {record[1]}무 {record[2]}패')
                        if not result_reader.confirm_visible(adb,self.stop,matchers[ranking_config['result']],matchers[ranking_config['confirm']]):
                            ranking_action=None
                            self.emit('랭킹 결과 판독 후 화면 변경 확인 · 이전 확인 좌표 클릭 없이 현재 화면 다시 확인')
                while ranking.events:self.emit(ranking.events.pop(0))
                if ranking.finished:
                    if cfg.get('_daily'):cfg['_ranking_done']=ranking.completion_reason or '오늘 허용된 도전 횟수 소진'
                    self.emit('랭킹 목표 횟수 완료 · 다음 연속 경기 시작 전에 중지');break
                if ranking.hold:
                    try:transition_watch.observe(('랭킹',ranking.phase),now,300)
                    except SafetyStop as error:
                        recover_transition(error);self.stop.wait(1);continue
                    if ranking_action:
                        if self.stop.is_set():break
                        _,matched_point,score,_=results[ranking_action]
                        point=click_point(records[ranking_action],matched_point,size)
                        if not cfg['dry']:adb.tap(point)
                        last_click=dict(button=ranking_action,point=point,time=now,dry=cfg['dry'])
                        self.emit(f'{"모의 랭킹 탭" if cfg["dry"] else "랭킹 탭"} {records[ranking_action]["name"]} 좌표={point} 점수={score:.3f}')
                        if ranking_action==ranking_config['proceed']:
                            machine=WorkflowMachine(records,rules,now,detection_frames=frames)
                    if now-heartbeat>=5:
                        labels={'lobby':'무료/별 갱신 상태 확인','series':'연속 경기 대기','lineup':'라인업 경기 진행 대기','departing':'경기 화면 진입 대기','playing':'경기 진행','result_confirm':'결과 확인 버튼 대기','returning':'결과 확인 후 로비 복귀 대기','reward_confirm':'15회 보상 팝업 확인 대기','reward_return':'15회 보상 수령 후 로비 확인 대기','points_confirm':'즉시 갱신 포인트 확인 대기','star_confirm':'즉시 갱신 별 확인 대기','refresh_return':'즉시 갱신 팝업 종료 대기'}
                        scores=' / '.join(f'{records[key]["name"]}={results[key][2]:.3f}({"인식" if results[key][0] else "미인식"})' for key in referenced if key in results)
                        progress_label=f'오늘 완료 {ranking.completed}회' if cfg.get('_daily') else f'완료 {ranking.completed}/{ranking.target}회'
                        self.emit(f'랭킹 대기 · {labels[ranking.phase]} · {progress_label} · {scores}')
                        heartbeat=now
                    self.stop.wait(interval);continue
                try:action = machine.observe({n: r[0] for n, r in results.items()}, now)
                except SafetyStop as error:
                    recover_transition(error);self.stop.wait(1);continue
                transition_state=None
                transition_limit=180
                if machine.active:
                    transition_state=('규칙',machine.active.get('name',machine.active['id']),machine.index+1,machine.phase)
                    record=records[machine.active['steps'][machine.index]['button']]
                    transition_limit=max(180,float(record.get('click_delay',0))+float(record.get('cooldown',2))+180)
                elif machine.normal.pending:
                    key=machine.normal.pending
                    transition_state=('클릭 확인',records[key]['name'])
                    record=records[key]
                    transition_limit=max(180,float(record.get('retry_limit',0))*max(float(record.get('retry_interval',8)),float(record.get('click_delay',0)),float(record.get('cooldown',2)))+180)
                try:transition_watch.observe(transition_state,now,transition_limit)
                except SafetyStop as error:
                    recover_transition(error);self.stop.wait(1);continue
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
                    league_tap(action,now)
                    last_click=dict(button=action,point=point,time=now,dry=cfg['dry'])
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
                        policy=records[machine.normal.pending]
                        required=policy.get('disappear_frames',3);limit=policy.get('retry_limit',0)
                        detail=f'설정한 간격 후 같은 버튼만 최대 {limit}회 재클릭 (현재 {machine.normal.retries}회)' if limit else '자동 재클릭 OFF'
                        if limit and machine.normal.retries>=limit:detail='자동 재클릭 한도 도달 · 추가 클릭 없이 대기'
                        self.emit(f'클릭 보류 · {name} 사라짐 확인 {machine.normal.absent}/{required}회 · {detail}')
                    previous_state, heartbeat = state, now
                self.stop.wait(interval)
        except Exception as e:
            cfg['_failed']=True
            self.emit(f'안전 중지 · {e}')
            if (recovery is not None and recovery.active and recovery.config['enabled']
                    and recovery.launches>=recovery.config['max_launches']
                    and not self.stop.is_set()):
                if cfg['dry']:
                    self.emit('모의 복구 실패 알림 · 실제 휴대폰 전송 안 함')
                elif notification_config.get('enabled'):
                    # One terminal exception per run, hence one alert. Do not
                    # send screenshots, device details, or raw exception text.
                    text=f'컴프야 자동 복구를 {recovery.launches}회 시도했지만 경기 화면으로 복귀하지 못해 매크로를 중지했습니다. PC에서 플레이어와 게임 상태를 확인해주세요.'
                    self.dispatch_notification(notification_config,text)
                    self.emit('자동 복구 한도 도달 · 휴대폰 알림 전송 요청')
                else:
                    self.emit('복구 실패 알림 미전송 · 휴대폰 알림 설정이 꺼져 있습니다.')
            if isinstance(e, SafetyStop):
                if cfg['dry']:
                    self.emit('모의 실행은 클릭하지 않습니다. 모의 탭 후 게임 버튼을 직접 눌러 전환을 확인하세요.')
                else:
                    self.emit('게임에서 버튼이 실제로 눌렸는지 확인하세요. 누르지 않았다면 등록 이미지·검색 영역을 다시 확인하세요.')
            if png is not None:
                try:
                    from diagnostics import write_stop_snapshot
                    context=dict(error=str(e),error_type=type(e).__name__,last_click=last_click,
                                 workflow=machine.status if machine else None,
                                 recovery_active=recovery.active if recovery else False,
                                 recovery_step=recovery.index if recovery else None,
                                 recovery_launches=recovery.launches if recovery else 0,
                                 ranking_phase=ranking.phase if ranking else None)
                    path = write_stop_snapshot(ROOT, png, records, results,
                                               machine.pending if machine else None, cfg['dry'],
                                               context=context,previous_png=previous_png)
                    self.emit(f'중지 직전 화면 저장 · {path}')
                    scores = ' / '.join(f'{records[n]["name"]}={r[2]:.3f} (기준 {records[n]["threshold"]:.3f})' for n, r in results.items())
                    self.emit('마지막 인식 · ' + scores)
                except Exception as diagnostic_error:
                    self.emit(f'중지 화면 저장 실패 · {diagnostic_error}')
        finally:
            if 'timing' in locals():
                timing.cancel();time_event()
            if not cfg.get('_scheduled'):self.stop.set()
            self.emit('실행 종료')

    def render_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0','end')
        history=self.log_history if self.detailed_log.get() else self.important_history
        for stamp,message in history:
            shown=message if self.detailed_log.get() else important_message(message)
            if shown is not None:
                tag='error' if any(word in message for word in ('안전 중지','실패','시간 초과')) else 'normal'
                self.log.insert('end',stamp+' '+shown+'\n',tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll(self):
        log_changed=False
        for _ in range(100):
            try:
                msg = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(msg, tuple):
                if msg[0]=='league_time':
                    if not self.closing:self.league_time_text.set(msg[1])
                    continue
                if msg[0]=='league_season':
                    if not self.closing:self.league_season_text.set(msg[1])
                    continue
                if msg[0]=='league_count':
                    if not self.closing:
                        self.league_count_text.set((f'{"모의 집계" if msg[2] else "이번 실행"} · 리그 완료 {msg[1]}경기 (시작마다 초기화)') if msg[3] else '리그 경기 집계 미사용 · 경기 중/결과 이미지를 선택하세요.')
                    continue
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
            self.status.set(concise_status(msg))
            entry=(time.strftime('%H:%M:%S'),msg)
            self.log_history.append(entry)
            if important_message(msg) is not None:self.important_history.append(entry)
            log_changed=True
        if log_changed:self.render_log()
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
        app.manage_recovery()
        app.manage_schedule()
        root.update()
        (ROOT/'logs'/'selftest.json').write_text(json.dumps({
            'frozen': bool(getattr(sys, 'frozen', False)), 'template_score': score,
            'gui': 'ok', 'capture_editor': 'ok', 'point_editor': 'ok', 'button_manager': 'ok', 'rule_manager': 'ok', 'progress_manager': 'ok', 'notification_manager': 'ok', 'ranking_manager': 'ok', 'recovery_manager': 'ok'}), encoding='utf-8')
        root.destroy()
    else:
        root.mainloop()
