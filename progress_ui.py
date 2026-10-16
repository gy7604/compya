import copy
import tkinter as tk
from tkinter import ttk,messagebox
from progress import validate_progress


class ProgressManager:
    def __init__(self,app):
        self.app=app
        draft=copy.deepcopy(app.progress)
        self.window=win=tk.Toplevel(app.root)
        win.title('진행 보조 클릭 · 두 이미지가 모두 없을 때 지정 위치 누르기')
        win.transient(app.root);win.grab_set()
        enabled=tk.BooleanVar(value=draft['enabled'])
        ttk.Checkbutton(win,text='진행 보조 클릭 사용',variable=enabled).grid(row=0,column=0,columnspan=2,padx=12,pady=8,sticky='w')
        records=app.records
        choices=['미지정']+[r['name'] for r in records]
        watch=draft.get('watch',[])
        def name(key):return next((r['name'] for r in records if r['id']==key),'미지정')
        def guess(word):return next((r['name'] for r in records if word in r['name'].lower().replace(' ','')),'미지정')
        first=tk.StringVar(value=name(watch[0]) if watch else guess('playball'))
        second=tk.StringVar(value=name(watch[1]) if len(watch)>1 else guess('포수리드'))
        for row,label,var in [(1,'이 이미지가 없고',first),(2,'이 이미지도 없으면',second)]:
            ttk.Label(win,text=label).grid(row=row,column=0,padx=12,pady=5,sticky='w')
            ttk.Combobox(win,textvariable=var,values=choices,state='readonly',width=40).grid(row=row,column=1,padx=12)
        delay=tk.StringVar(value=str(draft['delay']));interval=tk.StringVar(value=str(draft['interval']))
        for row,label,var in [(3,'둘 다 미인식 후 대기 (초)',delay),(4,'보조 클릭 반복 간격 (초)',interval)]:
            ttk.Label(win,text=label).grid(row=row,column=0,padx=12,pady=5,sticky='w')
            ttk.Entry(win,textvariable=var,width=20).grid(row=row,column=1,sticky='w',padx=12)
        point=draft.get('point') or ['', '']
        x=tk.StringVar(value=str(point[0]));y=tk.StringVar(value=str(point[1]))
        bar=ttk.Frame(win);bar.grid(row=5,column=0,columnspan=2,padx=12,pady=5,sticky='w')
        ttk.Label(bar,text='클릭 좌표 X ').pack(side='left');ttk.Entry(bar,textvariable=x,width=8).pack(side='left')
        ttk.Label(bar,text=' Y ').pack(side='left');ttk.Entry(bar,textvariable=y,width=8).pack(side='left')
        info=tk.StringVar(value=f'기준 해상도: {draft.get("size") or "미등록"}')
        ttk.Label(win,textvariable=info).grid(row=6,column=0,columnspan=2,padx=12,sticky='w')
        def pick():
            def received(png):
                if not win.winfo_exists():return
                from capture import point_editor
                def saved(point,size):
                    draft['size']=size;x.set(str(point[0]));y.set(str(point[1]))
                    info.set(f'기준 해상도: {size[0]}×{size[1]}')
                point_editor(win,png,saved)
            app.request_capture(received)
        ttk.Button(win,text='화면에서 보조 클릭 위치 선택',command=pick).grid(row=7,column=0,columnspan=2,pady=8)
        ttk.Label(win,text='일반 버튼 인식·이전 클릭 대기·조건 규칙 실행 중에는 보조 클릭을 하지 않습니다.\n어느 화면에서도 메뉴/확인 버튼과 겹치지 않는 여백을 직접 선택하세요.\n이닝 교대나 비디오판독 자체를 구별하는 기능은 아닙니다.',wraplength=560).grid(row=8,column=0,columnspan=2,padx=12,pady=8)
        def save():
            if app.capturing:
                messagebox.showinfo('캡처 중','좌표 선택이 끝난 뒤 저장하세요.',parent=win);return
            try:
                draft.update(enabled=enabled.get(),watch=[next((r['id'] for r in records if r['name']==v.get()),'') for v in (first,second)],delay=float(delay.get()),interval=float(interval.get()),point=[int(x.get()),int(y.get())] if x.get().strip() and y.get().strip() else None)
                validate_progress(draft,records)
                app.progress_store.save(draft,records)
                app.progress=draft
                win.destroy()
                app.emit('진행 보조 클릭 설정 저장 · '+('ON' if draft['enabled'] else 'OFF'))
            except Exception as e:messagebox.showerror('보조 클릭 저장 실패',str(e),parent=win)
        ttk.Button(win,text='저장',command=save).grid(row=9,column=0,pady=12)
        ttk.Button(win,text='취소',command=win.destroy).grid(row=9,column=1,pady=12)
