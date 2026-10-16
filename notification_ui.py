import copy
import secrets
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from notifications import validate_notifications,send_notification


class NotificationManager:
    def __init__(self,app):
        self.app=app
        self.window=win=tk.Toplevel(app.root)
        win.title('휴대폰 알림 · ntfy');win.transient(app.root);win.grab_set()
        draft=copy.deepcopy(app.notifications)
        enabled=tk.BooleanVar(value=draft['enabled']);stop=tk.BooleanVar(value=draft['stop'])
        topic=tk.StringVar(value=draft['topic'] or 'v26_'+secrets.token_hex(16))
        text=tk.StringVar(value=draft['message'])
        ttk.Checkbutton(win,text='이미지 감지 시 휴대폰 알림 사용',variable=enabled).pack(anchor='w',padx=12,pady=8)
        ttk.Label(win,text='1. Android에 ntfy 설치 → + → 아래 채널 이름을 그대로 구독\n2. 알림 권한 허용 → 테스트 알림 확인\n공개 서버 채널입니다. 채널 이름을 공유하거나 개인정보를 보내지 마세요.').pack(anchor='w',padx=12)
        ttk.Label(win,text='서버: https://ntfy.sh / 채널 이름').pack(anchor='w',padx=12,pady=(10,0))
        ttk.Entry(win,textvariable=topic,width=65).pack(fill='x',padx=12)
        ttk.Label(win,text='알림 이미지 (여러 개 선택 가능 · 3회 연속 인식 후 알림)').pack(anchor='w',padx=12,pady=(10,0))
        records=app.records
        box=tk.Listbox(win,selectmode='multiple',exportselection=False,height=8)
        box.pack(fill='both',expand=True,padx=12)
        for i,r in enumerate(records):
            box.insert('end',r['name'])
            if r['id'] in draft['watch']:box.selection_set(i)
        ttk.Label(win,text='알림 문구').pack(anchor='w',padx=12,pady=(8,0))
        ttk.Entry(win,textvariable=text,width=65).pack(fill='x',padx=12)
        ttk.Checkbutton(win,text='감지하면 클릭하기 전에 매크로 중지 (전송 실패 시에도 중지)',variable=stop).pack(anchor='w',padx=12,pady=8)
        ttk.Label(win,text='같은 이미지가 보이는 동안 한 번만 전송합니다.\n3회 연속 사라졌다 재등장하면 다시 알립니다. 모의 실행에서는 자동 전송하지 않습니다.').pack(anchor='w',padx=12)
        status=tk.StringVar(value='스크린샷은 전송하지 않습니다.')
        ttk.Label(win,textvariable=status).pack(anchor='w',padx=12,pady=8)
        def config():
            return dict(enabled=enabled.get(),watch=[records[i]['id'] for i in box.curselection()],topic=topic.get().strip(),message=text.get().strip(),stop=stop.get())
        def test():
            candidate=config()
            test_button.configure(state='disabled');status.set('테스트 알림 전송 중…')
            def task():
                try:send_notification(candidate,'V26 매크로 테스트 알림');result='테스트 전송 성공 · 휴대폰 알림을 확인하세요.'
                except Exception:result='테스트 전송 실패 · 인터넷과 채널 이름을 확인하세요.'
                app.events.put(('notification_test',self,result))
            threading.Thread(target=task,daemon=True).start()
        self.test_finished=lambda result:(status.set(result),test_button.configure(state='normal'))
        def save():
            try:
                candidate=config();validate_notifications(candidate,records)
                app.notification_store.save(candidate,records);app.notifications=candidate;win.destroy()
            except Exception as e:messagebox.showerror('알림 설정',str(e),parent=win)
        bar=ttk.Frame(win);bar.pack(fill='x',padx=12,pady=12)
        test_button=ttk.Button(bar,text='휴대폰으로 테스트 알림 보내기',command=test);test_button.pack(side='left')
        ttk.Button(bar,text='저장',command=save).pack(side='right')
        ttk.Button(bar,text='취소',command=win.destroy).pack(side='right',padx=8)
