import copy
import json
import time
import uuid
import tkinter as tk
from tkinter import ttk,messagebox
from ranking import validate_ranking
from ranking_capture import save_rank_image,save_rank_point

FIELDS=[('lobby','랭킹 로비 제목'),('refresh','활성 무료 갱신'),('points','일반 포인트 P 아이콘 (항상 사용)'),
        ('paid','로비 별 갱신 아이콘 (별 사용 시)'),('series','활성 연속 경기'),('disabled','비활성 연속 경기'),
        ('proceed','라인업 팝업 경기 진행'),('result','연속 경기 결과 제목'),('confirm','연속 경기 결과 확인'),
        ('reward','15회 글러브 수령 가능 (선택)'),('reward_confirm','보상 획득 제목 → 확인 좌표 (선택)'),
        ('refresh_dialog','즉시 갱신 팝업 제목'),('points_confirm','팝업 P 아이콘 → 결제 버튼 좌표'),('star_confirm','팝업 별 아이콘 → 결제 버튼 좌표')]

class RankingManager:
    def __init__(self,app):
        self.app=app;self.window=win=tk.Toplevel(app.root);win.title('랭킹 챌린지 설정 · 이 창에서 이미지 등록');win.transient(app.root);win.grab_set()
        win.geometry(f'{min(1100,win.winfo_screenwidth()-80)}x{min(820,win.winfo_screenheight()-100)}')
        self.draft=copy.deepcopy(app.ranking);self.values={};self.combos={}
        enabled=tk.BooleanVar(value=self.draft['enabled'])
        ttk.Checkbutton(win,text='랭킹 챌린지 연속 경기 모드 사용',variable=enabled).pack(anchor='w',padx=12,pady=8)
        ttk.Label(win,text='등록/교체 → 게임 화면에서 영역 선택 → 저장. 클릭할 항목은 클릭 위치도 선택할 수 있습니다.\n이미지·좌표 변경은 버튼 관리에도 바로 저장됩니다. 아래 저장은 랭킹 연결 설정을 저장합니다.').pack(anchor='w',padx=12)
        allow_star=tk.BooleanVar(value=self.draft.get('allow_star',False))
        ttk.Checkbutton(win,text='별 재화 갱신 사용 (기본 OFF) · 포인트 갱신은 항상 사용',variable=allow_star).pack(anchor='w',padx=12,pady=5)
        container=ttk.Frame(win);container.pack(fill='both',expand=True,padx=12,pady=8)
        canvas=tk.Canvas(container,highlightthickness=0);scroll=ttk.Scrollbar(container,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        body=ttk.Frame(canvas);item=canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        for row,(key,label) in enumerate(FIELDS):
            self.values[key]=tk.StringVar(value=next((r['name'] for r in app.records if r['id']==self.draft.get(key)),'미사용'))
            ttk.Label(body,text=label).grid(row=row,column=0,padx=4,pady=6,sticky='w')
            combo=ttk.Combobox(body,textvariable=self.values[key],state='readonly',width=27);combo.grid(row=row,column=1,padx=4,sticky='ew');self.combos[key]=combo
            ttk.Button(body,text='등록/교체',command=lambda k=key,l=label:self.capture(k,l)).grid(row=row,column=2,padx=3)
            if key not in ('lobby','disabled','result','refresh_dialog'):
                ttk.Button(body,text='클릭 위치',command=lambda k=key:self.point(k)).grid(row=row,column=3,padx=3)
            ttk.Button(body,text='인식 테스트',command=lambda k=key:self.test(k)).grid(row=row,column=4,padx=3)
        body.columnconfigure(1,weight=1)
        self.refresh()
        ttk.Label(body,text='활성 연속 경기 우선 → 비활성 확인 시 무료/포인트 갱신. 별 갱신은 위 체크를 켰을 때만 사용합니다.\n팝업 제목은 즉시 갱신, 결제 조건은 해당 버튼 안의 P/별 아이콘을 숫자 없이 캡처하세요.\n팝업 결제 항목의 클릭 위치는 각각 결제 버튼 중앙으로 지정해야 합니다. 활성/비활성 버튼은 배경까지 각각 캡처합니다.\n15회 보상은 글러브·15·보상 받기 표시를 함께 캡처하고 클릭 위치를 글러브 중앙으로 지정하세요.\n보상 팝업은 고유한 보상 획득 제목을 캡처하고 클릭 위치는 하단 확인 버튼으로 지정하세요.\n두 보상 항목과 각각의 클릭 위치를 함께 지정하면 보상 수령을 사용합니다.\n결과 화면을 1회로 집계합니다. 보상 수령 사용 시 목표 완료 후 로비 보상 처리까지 하고 중지합니다.\n시작마다 완료 횟수는 0으로 초기화됩니다. 모의 실행에서는 게임 버튼을 직접 눌러 화면을 전환하세요.',wraplength=1000).grid(row=len(FIELDS),column=0,columnspan=5,padx=4,pady=10,sticky='w')
        self.status=tk.StringVar(value='랭킹 로비에서 시작하세요. 화면 캡처는 게임 버튼을 누르지 않습니다.')
        ttk.Label(win,textvariable=self.status,wraplength=1000).pack(anchor='w',padx=12)
        bar=ttk.Frame(win);bar.pack(fill='x',padx=12,pady=10)
        def save():
            if app.capturing:messagebox.showinfo('캡처 중','캡처가 끝난 뒤 저장하세요.',parent=win);return
            try:
                candidate=dict(enabled=enabled.get(),allow_star=allow_star.get(),**{key:self.selected(key)['id'] if self.selected(key) else '' for key in self.values})
                validate_ranking(candidate,app.records);app.ranking_store.save(candidate,app.records);app.ranking=candidate;win.destroy()
            except Exception as e:messagebox.showerror('랭킹 설정',str(e),parent=win)
        ttk.Button(bar,text='저장',command=save).pack(side='right')
        ttk.Button(bar,text='닫기',command=win.destroy).pack(side='right',padx=8)
    def selected(self,key):return next((r for r in self.app.records if r['name']==self.values[key].get()),None)
    def refresh(self):
        for combo in self.combos.values():combo.configure(values=['미사용']+[r['name'] for r in self.app.records])
        if hasattr(self.app,'button_summary'):
            self.app.button_summary.set(f'등록 {len(self.app.records)}개 / 사용 {sum(r["enabled"] for r in self.app.records)}개')
    def capture(self,key,label):
        selected=self.selected(key);selected_id=selected['id'] if selected else None
        path=self.app.registry.root/'assets'/'buttons'/f'ranking_{uuid.uuid4().hex}_{time.time_ns()}.png'
        def received(png):
            if not self.window.winfo_exists():return
            from capture import editor
            def saved(saved_path,roi):
                size=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['size']
                record=save_rank_image(self.app,selected_id,label,saved_path,roi,size)
                self.refresh();self.values[key].set(record['name']);self.window.grab_set()
                self.status.set(f'{label} 이미지 등록 완료 · 랭킹 연결은 아래 저장을 눌러주세요.')
            editor(self.window,png,path,saved)
        self.app.request_capture(received)
    def point(self,key):
        record=self.selected(key)
        if not record:messagebox.showinfo('클릭 위치','먼저 이미지를 등록하세요.',parent=self.window);return
        def received(png):
            if not self.window.winfo_exists():return
            from capture import point_editor
            def saved(point,size):
                save_rank_point(self.app,record['id'],point,size);self.refresh();self.window.grab_set();self.status.set(f'클릭 위치 저장 완료 · {point}')
            point_editor(self.window,png,saved)
        self.app.request_capture(received)
    def test(self,key):
        record=self.selected(key)
        if not record:messagebox.showinfo('인식 테스트','먼저 이미지를 등록하세요.',parent=self.window);return
        try:
            from vision import Matcher
            from clicks import click_point
            extra={'color_tolerance':25} if key in ('series','disabled','refresh','points','paid','reward','points_confirm','star_confirm') else {}
            matcher=Matcher(self.app.registry.resolve(record['template']),record['threshold'],record['roi'],**extra)
            def received(png):
                if not self.window.winfo_exists():return
                found,center,score,size=matcher.find(png)
                if record.get('size') and tuple(record['size'])!=size:raise ValueError('등록 해상도와 다릅니다. 다시 캡처하세요.')
                target=click_point(record,center,size)
                messagebox.showinfo('인식 테스트 · 클릭 안 함',f'{"인식" if found else "미인식"} · 점수 {score:.3f} / 기준 {record["threshold"]:.3f}\n인식 위치 {center} / 클릭 예정 {target}',parent=self.window)
            self.app.request_capture(received)
        except Exception as e:messagebox.showerror('인식 테스트',str(e),parent=self.window)
