import copy
import tkinter as tk
from tkinter import ttk,messagebox
from ranking import validate_ranking

class RankingManager:
    def __init__(self,app):
        self.window=win=tk.Toplevel(app.root);win.title('랭킹 챌린지 설정');win.transient(app.root);win.grab_set()
        draft=copy.deepcopy(app.ranking);enabled=tk.BooleanVar(value=draft['enabled'])
        ttk.Checkbutton(win,text='랭킹 챌린지 연속 경기 모드 사용',variable=enabled).grid(row=0,column=0,columnspan=2,padx=12,pady=10,sticky='w')
        ttk.Label(win,text='랭킹 로비에서 시작하세요. 이미지는 버튼 관리에서 등록하고 사용을 켭니다.\n랭킹 전용 이미지는 감지 전용 또는 규칙 전용으로 설정하세요.').grid(row=1,column=0,columnspan=2,padx=12,sticky='w')
        records=app.records;labels=['미사용']+[r['name'] for r in records];values={}
        for row,(key,label) in enumerate([('lobby','랭킹 로비 제목 (복귀 감지)'),('refresh','활성화된 무료 갱신'),('points','일반 포인트 P 아이콘 (선택)'),('paid','별 재화 갱신 (선택 · 클릭 안 함)'),('series','활성화된 연속 경기 버튼'),('disabled','비활성화된 연속 경기 버튼 (감지)'),('proceed','투수 라인업 팝업의 경기 진행'),('result','연속 경기 결과 제목 (감지)'),('confirm','연속 경기 결과의 확인 버튼')],2):
            values[key]=tk.StringVar(value=next((r['name'] for r in records if r['id']==draft[key]),'미사용'))
            ttk.Label(win,text=label).grid(row=row,column=0,padx=12,pady=8,sticky='w')
            ttk.Combobox(win,textvariable=values[key],values=labels,state='readonly',width=35).grid(row=row,column=1,padx=12)
        ttk.Label(win,text='활성 연속 경기 → 경기 진행을 먼저 누릅니다.\n연속 경기 비활성을 확인했을 때만 무료 또는 일반 포인트로 갱신합니다.\n별 재화 갱신이 감지되면 갱신을 건너뛰고 연속 경기를 진행합니다.\nP 아이콘은 갱신 버튼 안에서 숫자를 빼고 캡처하고 검색 영역을 좁히세요.\n포인트 금액이 바뀌어도 갱신합니다. 별 재화는 누르지 않습니다.\n허용된 갱신이 없고 연속 경기도 비활성이면 대기합니다.\n경기 화면 진입 후 연속 경기 결과 제목을 확인하면 1회로 셉니다.\n개별 경기 수는 집계하지 않습니다. 목표 반복 수는 메인 화면에 입력하세요.\n매크로 시작 시 완료 횟수는 0으로 초기화됩니다.\n목표 달성 시 결과 화면에서 중지합니다. 남은 횟수가 있으면 결과 확인 후 반복합니다.').grid(row=11,column=0,columnspan=2,padx=12,pady=10,sticky='w')
        def save():
            try:
                candidate=dict(enabled=enabled.get(),**{key:next((r['id'] for r in records if r['name']==var.get()),'') for key,var in values.items()})
                validate_ranking(candidate,records);app.ranking_store.save(candidate,records);app.ranking=candidate;win.destroy()
            except Exception as e:messagebox.showerror('랭킹 설정',str(e),parent=win)
        ttk.Button(win,text='저장',command=save).grid(row=12,column=1,pady=12)
