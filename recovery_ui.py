import copy
import json
import time
import uuid
import tkinter as tk
from tkinter import ttk,messagebox
from recovery import default_recovery
from ranking_capture import save_rank_image,save_rank_point

class RecoveryManager:
    def __init__(self,app):
        self.app=app;self.window=win=tk.Toplevel(app.root);win.title('자동 재접속 · 광고 닫기 · 리그/랭킹 복귀');win.transient(app.root);win.grab_set()
        win.geometry(f'{min(1050,win.winfo_screenwidth()-80)}x{min(850,win.winfo_screenheight()-100)}')
        draft=copy.deepcopy(app.recovery);self.routes=draft['steps'];self.popups=draft['popups'];self.trees={}
        enabled=tk.BooleanVar(value=draft['enabled']);package=tk.StringVar(value=draft['package']);limit=tk.StringVar(value=str(draft['max_launches']))
        ttk.Checkbutton(win,text='선택한 모드에서 게임 종료 시 자동 재실행 및 복귀 사용',variable=enabled).pack(anchor='w',padx=12,pady=7)
        mode_row=ttk.Frame(win);mode_row.pack(fill='x',padx=12,pady=4)
        ttk.Label(mode_row,text='복귀 대상 모드 ').pack(side='left')
        target=tk.StringVar(value='일반 리그' if draft.get('target','ranking')=='league' else '랭킹 챌린지')
        ttk.Combobox(mode_row,textvariable=target,values=['일반 리그','랭킹 챌린지'],state='readonly',width=20).pack(side='left')
        row=ttk.Frame(win);row.pack(fill='x',padx=12)
        ttk.Label(row,text='게임 패키지 ').pack(side='left');ttk.Entry(row,textvariable=package,width=60).pack(side='left')
        ttk.Label(row,text=' 재실행 한도 ').pack(side='left');ttk.Entry(row,textvariable=limit,width=4).pack(side='left')
        stall_row=ttk.Frame(win);stall_row.pack(fill='x',padx=12,pady=5)
        stall=tk.BooleanVar(value=draft.get('stall_enabled',False));stall_seconds=tk.StringVar(value=str(draft.get('stall_seconds',180)))
        ttk.Checkbutton(stall_row,text='화면 정지·진행 없음 감지 후 재접속',variable=stall).pack(side='left')
        ttk.Entry(stall_row,textvariable=stall_seconds,width=6).pack(side='left')
        ttk.Label(stall_row,text='초 (60~1800, 기본 180초 · 자동 복귀 사용 시 적용)').pack(side='left')
        player_row=ttk.Frame(win);player_row.pack(fill='x',padx=12,pady=4)
        restart_player=tk.BooleanVar(value=draft.get('restart_player',True));player_index=tk.StringVar(value=str(draft.get('player_index',0)))
        player_console=tk.StringVar(value=draft.get('player_console',''))
        ttk.Checkbutton(player_row,text='멈춤 시 게임 대신 LDPlayer 재시작',variable=restart_player).pack(side='left')
        ttk.Label(player_row,text=' 인스턴스 번호 ').pack(side='left');ttk.Entry(player_row,textvariable=player_index,width=5).pack(side='left')
        console_row=ttk.Frame(win);console_row.pack(fill='x',padx=12)
        ttk.Label(console_row,text='ldconsole.exe 경로 (빈칸: ADB와 같은 폴더)').pack(side='left')
        ttk.Entry(console_row,textvariable=player_console,width=48).pack(side='left')
        goalrow=ttk.Frame(win);goalrow.pack(fill='x',padx=12,pady=7)
        ttk.Label(goalrow,text='복귀 완료 화면 (리그: 포수리드 등 경기 화면 / 랭킹: 로비)').pack(side='left')
        self.goal=tk.StringVar(value=next((r['name'] for r in app.records if r['id']==draft['goal']),'미지정'))
        self.goal_combo=ttk.Combobox(goalrow,textvariable=self.goal,state='readonly',width=35);self.goal_combo.pack(side='left',padx=8)
        ttk.Button(goalrow,text='완료 이미지 등록/교체',command=self.capture_goal).pack(side='left')
        self.choice=tk.StringVar();self.name=tk.StringVar(value='접속 화면')
        bar=ttk.Frame(win);bar.pack(fill='x',padx=12)
        ttk.Label(bar,text='기존 이미지 ').pack(side='left');self.choice_combo=ttk.Combobox(bar,textvariable=self.choice,state='readonly',width=35);self.choice_combo.pack(side='left')
        ttk.Label(bar,text=' 새 이미지 이름 ').pack(side='left');ttk.Entry(bar,textvariable=self.name,width=25).pack(side='left')
        for kind,label in [('steps','접속부터 목표 모드까지 클릭 순서'),('popups','접속 광고·이벤트 팝업 닫기 (먼저 처리)')]:
            frame=ttk.LabelFrame(win,text=label,padding=5);frame.pack(fill='both',expand=True,padx=12,pady=6)
            tree=ttk.Treeview(frame,columns=('name',),show='headings',height=4);tree.heading('name',text='등록 이미지');tree.pack(fill='both',expand=True);self.trees[kind]=tree
            controls=ttk.Frame(frame);controls.pack(fill='x')
            for text,fn in [('선택 이미지 추가',lambda k=kind:self.add(k)),('새 이미지 캡처',lambda k=kind:self.capture(k,False)),('선택 교체',lambda k=kind:self.capture(k,True)),('클릭 위치',lambda k=kind:self.point(k)),('삭제',lambda k=kind:self.move(k,None)),('위로',lambda k=kind:self.move(k,-1)),('아래로',lambda k=kind:self.move(k,1))]:
                ttk.Button(controls,text=text,command=fn).pack(side='left',padx=2,pady=4)
        ttk.Label(win,text='복귀 대상이 현재 모드와 같고 사용을 켰을 때만 이미지를 검사합니다. 필요 없으면 사용을 끄세요.\n리그 순서: 터치하여 시작(필요 시) → 메인 화면 PLAY BALL → 리그모드 → ResumePlay.\n랭킹 순서: 터치하여 시작 → 메인 로비 게임 메뉴 → 랭킹 챌린지.\n광고는 고유한 팝업 제목/그림을 캡처하고 닫기 X의 클릭 위치를 지정하세요. 공통 X만 등록하지 마세요.\n진입·광고 이미지는 감지 전용/규칙 전용으로 사용 ON. 완료 이미지는 기존 포수리드도 선택할 수 있습니다. 미완성이어도 저장 가능하며 실행할 때 검사합니다.\n재접속 중 일반 클릭은 보류하고, 복귀 후 리그 경기 수/랭킹 연속 경기 완료 횟수를 보존합니다. 진행 중이던 미완료 묶음은 집계하지 않습니다.\n이 기능은 게임 앱 종료를 감지합니다. ADB 연결 실패 자체는 자동 복구하지 않습니다. 화면 멈춤 감시는 별도로 켜세요.\n정상 대기 화면도 멈춤으로 판단할 수 있으니 감지 시간을 충분히 설정하세요. 재실행 한도를 함께 적용합니다.',wraplength=1000).pack(anchor='w',padx=12,pady=6)
        self.status=tk.StringVar(value='이미지를 캡처하는 동안 실제 게임을 클릭하지 않습니다.')
        ttk.Label(win,textvariable=self.status).pack(anchor='w',padx=12)
        def save():
            if app.capturing:return
            try:
                candidate=dict(restart_player=restart_player.get(),player_console=player_console.get().strip(),player_index=int(player_index.get()),stall_enabled=stall.get(),stall_seconds=int(stall_seconds.get()),enabled=enabled.get(),target='league' if target.get()=='일반 리그' else 'ranking',package=package.get().strip(),max_launches=int(limit.get()),goal=self.id_for(self.goal.get()),steps=self.routes,popups=self.popups)
                app.recovery_store.save(candidate);app.recovery=copy.deepcopy(candidate);win.destroy()
            except Exception as e:messagebox.showerror('재접속 설정',str(e),parent=win)
        ttk.Button(win,text='설정 저장',command=save).pack(anchor='e',padx=12,pady=8)
        self.refresh()
    def id_for(self,name):return next((r['id'] for r in self.app.records if r['name']==name),'')
    def record(self,key):return next((r for r in self.app.records if r['id']==key),None)
    def items(self,kind):return self.routes if kind=='steps' else self.popups
    def refresh(self):
        choices=['미지정']+[r['name'] for r in self.app.records];self.goal_combo.configure(values=choices);self.choice_combo.configure(values=choices)
        for kind,tree in self.trees.items():
            tree.delete(*tree.get_children())
            for i,key in enumerate(self.items(kind)):
                r=self.record(key);tree.insert('','end',iid=str(i),values=(f'{i+1}. {r["name"] if r else "미등록"}',))
        self.app.button_summary.set(f'등록 {len(self.app.records)}개 / 사용 {sum(r["enabled"] for r in self.app.records)}개')
    def add(self,kind):
        key=self.id_for(self.choice.get())
        if key:self.items(kind).append(key);self.refresh()
    def selected(self,kind):
        selected=self.trees[kind].selection();return int(selected[0]) if selected else None
    def move(self,kind,offset):
        index=self.selected(kind);items=self.items(kind)
        if index is None:return
        if offset is None:items.pop(index)
        elif 0<=index+offset<len(items):items[index],items[index+offset]=items[index+offset],items[index]
        self.refresh()
    def capture_image(self,key,label,on_saved):
        path=self.app.registry.root/'assets'/'buttons'/f'recovery_{uuid.uuid4().hex}_{time.time_ns()}.png'
        def received(png):
            if not self.window.winfo_exists():return
            from capture import editor
            def saved(saved_path,roi):
                size=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['size']
                record=save_rank_image(self.app,key,'복구 · '+label,saved_path,roi,size);on_saved(record);self.refresh();self.window.grab_set()
            editor(self.window,png,path,saved)
        self.app.request_capture(received)
    def capture_goal(self):
        self.capture_image(self.id_for(self.goal.get()),'목표 모드 복귀 완료',lambda record:self.goal.set(record['name']))
    def capture(self,kind,replacing):
        index=self.selected(kind) if replacing else None
        if replacing and index is None:return
        key=self.items(kind)[index] if index is not None else None
        def saved(record):
            if index is None:self.items(kind).append(record['id'])
            else:self.items(kind)[index]=record['id']
            self.status.set('이미지 등록 완료 · 필요한 클릭 위치를 지정하세요.')
        self.capture_image(key,self.name.get().strip() or '접속 화면',saved)
    def point(self,kind):
        index=self.selected(kind)
        if index is None:return
        key=self.items(kind)[index]
        def received(png):
            if not self.window.winfo_exists():return
            from capture import point_editor
            def saved(point,size):
                save_rank_point(self.app,key,point,size);self.window.grab_set();self.status.set(f'클릭 위치 저장 · {point}')
            point_editor(self.window,png,saved)
        self.app.request_capture(received)
