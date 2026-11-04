"""PC-only pairing UI. Credentials never enter logs or the public release."""
import tkinter as tk
from tkinter import ttk,messagebox
from urllib.parse import quote
from mobile_control import MobileStore


class MobileManager:
    def __init__(self,app):
        self.app=app;self.window=tk.Toplevel(app.root);self.window.title('휴대폰 웹 제어');self.window.transient(app.root)
        self.window.geometry('480x640');self.store=MobileStore(app.mobile_root)
        try:self.config=self.store.load()
        except Exception:self.config=None
        ttk.Label(self.window,text='휴대폰에서 시작·중지 / 상태 / 게임 화면 확인',font=('',12,'bold')).pack(pady=12)
        if not self.config:
            ttk.Label(self.window,text='모바일 서버 연결 준비가 필요합니다.\n기존 매크로는 그대로 사용할 수 있습니다.',wraplength=430).pack(pady=15)
            return
        self.enabled=tk.BooleanVar(value=self.config['enabled'])
        ttk.Checkbutton(self.window,text='휴대폰 제어 연결 사용',variable=self.enabled,command=self.apply).pack(anchor='w',padx=18)
        ttk.Label(self.window,text='QR을 휴대폰 카메라로 읽으면 본인 연결 암호로 로그인합니다.\n이 QR과 연결 암호는 다른 사람에게 공유하지 마세요.',wraplength=440).pack(pady=10)
        self.canvas=tk.Canvas(self.window,width=300,height=300,bg='white',highlightthickness=0);self.canvas.pack()
        ttk.Button(self.window,text='내 휴대폰 연결 QR 표시',command=self.show_qr).pack(pady=8)
        ttk.Label(self.window,text=self.config['url'],wraplength=440).pack(pady=6)
        ttk.Button(self.window,text='웹 주소 복사',command=lambda:self.copy(self.config['url'])).pack()
        ttk.Button(self.window,text='연결 암호 복사',command=lambda:self.copy(self.config['owner_key'])).pack(pady=7)
        ttk.Label(self.window,text='PC 프로그램을 열어 두어야 합니다.\n시작은 PC에 설정된 모드·일정·모의 실행 여부를 그대로 사용합니다.\n게임 설정 확인 체크가 해제돼 있으면 휴대폰 시작도 제한됩니다.',wraplength=440).pack(pady=8)

    def copy(self,value):
        self.window.clipboard_clear();self.window.clipboard_append(value)

    def show_qr(self):
        import qrcode
        code=qrcode.QRCode(border=3);code.add_data(self.config['url']+'/#key='+quote(self.config['owner_key']));code.make(fit=True)
        matrix=code.get_matrix();unit=max(1,300//len(matrix));offset=(300-len(matrix)*unit)//2
        self.canvas.delete('all')
        for y,row in enumerate(matrix):
            for x,black in enumerate(row):
                if black:self.canvas.create_rectangle(offset+x*unit,offset+y*unit,offset+(x+1)*unit,offset+(y+1)*unit,fill='black',outline='')

    def apply(self):
        try:
            self.config['enabled']=self.enabled.get();self.store.save(self.config);self.app.configure_mobile()
        except Exception:messagebox.showerror('모바일 연결','연결 설정을 저장하지 못했습니다.',parent=self.window)
