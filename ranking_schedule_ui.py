"""Program-internal scheduling settings; no external recurring jobs."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox
from ranking_schedule import validate


class ScheduleManager:
    def __init__(self, app):
        win = tk.Toplevel(app.root)
        win.title('한국시간 랭킹 일정')
        win.transient(app.root);win.grab_set()
        draft = copy.deepcopy(app.schedule)
        enabled = tk.BooleanVar(value=draft['enabled'])
        ttk.Checkbutton(win, text='프로그램 내부 일정 사용', variable=enabled).pack(anchor='w',padx=12,pady=8)
        ocr=tk.BooleanVar(value=draft.get('ocr_prices',False))
        ttk.Checkbutton(win,text='변경되는 갱신 금액을 Windows 기본 글자 인식으로 확인',variable=ocr).pack(anchor='w',padx=12)
        notice=tk.BooleanVar(value=draft.get('completion_notice',False))
        ttk.Checkbutton(win,text='하루 완료 후 ntfy 요약 알림 · 총 경기/승무패 (휴대폰 알림 사용 필요)',variable=notice).pack(anchor='w',padx=12)
        ttk.Label(win, text='월~금 13:00부터 무료 갱신 → P포인트\n토·일 20:00부터 무료 갱신 → P포인트 → 별\n늦게 켜면 즉시 시작 · 실행 중이면 리그 결과 확인 후 전환\n오늘 완료 상태와 결제 대기 기록은 재시작 후에도 보존합니다.').pack(anchor='w',padx=12,pady=6)
        ttk.Label(win, text='시작을 누른 채 프로그램과 PC를 켜 두어야 합니다.\n금액 미확인·구매 결과 미확인·하루 한도 도달 시 추가 구매를 보류합니다.\n글자 인식을 사용하면 금액이 바뀌어도 3개 화면에서 두 방식의 판독이 모두 일치해야 구매합니다.').pack(anchor='w',padx=12)
        records = {r['id']:r for r in app.records}
        route = tk.Listbox(win, height=6, width=65)
        route.pack(fill='x',padx=12,pady=8)
        ids = list(draft['ranking_steps'])
        def refresh():
            route.delete(0,'end')
            for key in ids:route.insert('end',records.get(key,{}).get('name',key))
        refresh()
        choice = ttk.Combobox(win,state='readonly',values=[r['name'] for r in app.records if r['enabled'] and r.get('kind')!='normal'],width=60)
        choice.pack(padx=12)
        bar=ttk.Frame(win);bar.pack(pady=5)
        def add():
            record=next((r for r in app.records if r['name']==choice.get()),None)
            if record and record['id'] not in ids:ids.append(record['id']);refresh()
        def remove():
            if route.curselection():ids.pop(route.curselection()[0]);refresh()
        ttk.Button(bar,text='진입 단계 추가',command=add).pack(side='left')
        ttk.Button(bar,text='선택 단계 제거',command=remove).pack(side='left')
        fields={}
        form=ttk.Frame(win);form.pack(fill='x',padx=12)
        for row,(key,label) in enumerate([('points_price','P 결제 금액 확인 이미지'),('star_price','별 결제 금액 확인 이미지'),('exhausted','오늘 횟수 소진 이미지 (선택)')]):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w')
            fields[key]=tk.StringVar(value=records.get(draft.get(key),{}).get('name','미사용'))
            ttk.Combobox(form,textvariable=fields[key],state='readonly',values=['미사용']+[r['name'] for r in app.records if r['enabled']],width=42).grid(row=row,column=1)
        limits={}
        for row,key in enumerate(('free','points','star'),start=3):
            ttk.Label(form,text={'free':'무료','points':'P포인트','star':'별'}[key]+' 하루 갱신 한도').grid(row=row,column=0,sticky='w')
            limits[key]=tk.StringVar(value=str(draft[key+'_limit']))
            ttk.Spinbox(form,from_=1,to=100,textvariable=limits[key],width=8).grid(row=row,column=1,sticky='w')
        ttk.Label(win,text='이미지 방식은 재화와 숫자 금액을 함께 등록하세요.\n글자 인식 방식은 PriceOCR.exe와 숫자가 제외된 재화 아이콘 이미지가 필요합니다.\n별 재화가 확인되지 않으면 별 결제를 하지 않습니다. 설정 변경은 중지 후 가능합니다.').pack(anchor='w',padx=12,pady=8)
        def save():
            try:
                draft.update(enabled=enabled.get(),ranking_steps=ids,ocr_prices=ocr.get(),completion_notice=notice.get())
                for key,value in fields.items():draft[key]=next((r['id'] for r in app.records if r['name']==value.get()),'')
                for key,value in limits.items():draft[key+'_limit']=int(value.get())
                validate(draft,app.records,app.ranking,app.recovery)
                app.schedule_store.save(draft);app.schedule=draft
                app.status.set('한국시간 랭킹 일정 저장 · 시작 버튼으로 실행')
                win.destroy()
            except Exception as error:messagebox.showerror('일정 설정',str(error),parent=win)
        ttk.Button(win,text='저장',command=save).pack(side='right',padx=12,pady=12)
