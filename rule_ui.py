import copy
import tkinter as tk
from tkinter import ttk, messagebox
from registry import new_button
from rules import new_rule


class RuleManager:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('조건 규칙 · 이미지 감지 후 순서대로 클릭')
        self.window.geometry('900x480')
        self.window.transient(app.root)
        self.window.grab_set()
        self.tree = ttk.Treeview(self.window, columns=('use', 'name', 'trigger', 'steps', 'finish'), show='headings')
        for key, label, width in [('use','사용',50),('name','규칙 이름',220),('trigger','감지 조건',180),('steps','단계 수',70),('finish','완료 후',100)]:
            self.tree.heading(key,text=label); self.tree.column(key,width=width)
        self.tree.pack(fill='both',expand=True,padx=10,pady=10)
        self.tree.bind('<Double-1>',lambda e:self.edit())
        bar=ttk.Frame(self.window); bar.pack(fill='x',padx=10)
        for text,fn in [('규칙 추가',lambda:self.edit(True)),('수정',self.edit),('사용 / 해제',self.toggle),
                        ('위로 ↑',lambda:self.move(-1)),('아래로 ↓',lambda:self.move(1)),('삭제',self.delete)]:
            ttk.Button(bar,text=text,command=fn).pack(side='left',padx=3)
        ttk.Button(self.window,text='목표 달성 → 시뮬레이션 예시 만들기 (기본 OFF)',command=self.preset).pack(pady=8)
        ttk.Label(self.window,text='위 규칙부터 확인합니다. 규칙 실행 중에는 일반 클릭을 멈추며, 조건 이미지는 클릭하지 않습니다.').pack()
        ttk.Button(self.window,text='닫기',command=self.window.destroy).pack(pady=6)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        records={r['id']:r for r in self.app.records}
        for r in self.app.rules:
            self.tree.insert('', 'end', iid=r['id'],values=('ON' if r['enabled'] else 'OFF',r['name'],
                records.get(r['trigger'],{}).get('name','미지정 / 삭제됨'),len(r['steps']),'중지' if r['finish']=='stop' else '일반 클릭 재개'))

    def selected(self):
        ids=self.tree.selection()
        if not ids:
            messagebox.showinfo('규칙 선택','목록에서 규칙을 선택하세요.',parent=self.window)
            return None
        return next(r for r in self.app.rules if r['id']==ids[0])

    def commit(self, rules):
        self.app.rule_store.save(rules,self.app.records)
        self.app.rules=rules
        self.refresh()

    def toggle(self):
        r=self.selected()
        if r is None:return
        rules=copy.deepcopy(self.app.rules)
        next(x for x in rules if x['id']==r['id'])['enabled']=not r['enabled']
        try:self.commit(rules)
        except Exception as e:messagebox.showerror('사용 설정 실패',str(e),parent=self.window)

    def move(self, offset):
        r=self.selected()
        if r is None:return
        rules=copy.deepcopy(self.app.rules)
        i=next(i for i,x in enumerate(rules) if x['id']==r['id']); j=i+offset
        if 0<=j<len(rules):
            rules[i],rules[j]=rules[j],rules[i]
            try:self.commit(rules); self.tree.selection_set(r['id'])
            except Exception as e:messagebox.showerror('저장 실패',str(e),parent=self.window)

    def delete(self):
        r=self.selected()
        if r and messagebox.askyesno('규칙 삭제',f'{r["name"]} 규칙을 삭제할까요?',parent=self.window):
            try:self.commit([x for x in self.app.rules if x['id']!=r['id']])
            except Exception as e:messagebox.showerror('삭제 실패',str(e),parent=self.window)

    def preset(self):
        if any(r['name']=='목표 달성 → 시뮬레이션' for r in self.app.rules):
            messagebox.showinfo('예시','이미 예시가 있습니다. 목록에서 수정하세요.',parent=self.window); return
        records=copy.deepcopy(self.app.records)
        made=[]
        for name,kind in [('목표 달성 조건','condition'),('일시정지 (규칙)','step'),('시뮬레이션 (규칙)','step'),
                          ('확인 (규칙)','step'),('시뮬레이션 진행 확인','condition')]:
            record=next((r for r in records if r['name']==name and r.get('kind')==kind),None)
            if record is None:
                record=new_button(name)
                if any(r['name']==name for r in records):record['name']+=' (예시)'
                record['kind']=kind; records.append(record)
            made.append(record)
        rule=new_rule('목표 달성 → 시뮬레이션')
        rule.update(trigger=made[0]['id'],success=made[4]['id'],steps=[{'button':r['id'],'verify':'next'} for r in made[1:4]])
        try:
            self.app.registry.save(records); self.app.records=records
            self.commit(self.app.rules+[rule])
            self.app.button_summary.set(f'등록 {len(records)}개 / 사용 {sum(r["enabled"] for r in records)}개')
            messagebox.showinfo('예시 생성 완료','예시는 OFF입니다. 이 창을 닫고 버튼 관리에서 5개 이미지를 캡처해 사용을 켠 뒤, 규칙을 수정·확인하고 사용을 켜세요.\n실제 게임의 화면 순서가 다르면 단계를 바꾸세요.',parent=self.window)
        except Exception as e:messagebox.showerror('예시 생성 실패',str(e),parent=self.window)

    def edit(self, adding=False):
        original=new_rule() if adding else self.selected()
        if original is None:return
        draft=copy.deepcopy(original)
        win=tk.Toplevel(self.window); win.title('조건 규칙 수정'); win.transient(self.window); win.grab_set()
        def restore(event):
            if event.widget==win and self.window.winfo_exists():self.window.grab_set()
        win.bind('<Destroy>',restore)
        name=tk.StringVar(value=draft['name']); enabled=tk.BooleanVar(value=draft['enabled'])
        ttk.Label(win,text='규칙 이름').grid(row=0,column=0,padx=10,pady=5)
        ttk.Entry(win,textvariable=name,width=48).grid(row=0,column=1,columnspan=3)
        records=self.app.records
        conditions=[r for r in records if r.get('kind')=='condition']
        targets=[r for r in records if r.get('kind')=='step']
        cond_labels=['미지정']+[r['name'] for r in conditions]
        def cond_name(key):return next((r['name'] for r in conditions if r['id']==key),'미지정')
        trigger=tk.StringVar(value=cond_name(draft['trigger'])); success=tk.StringVar(value=cond_name(draft['success']))
        for row,label,var in [(1,'이 이미지가 보이면 (감지 전용)',trigger),(2,'최종 확인 이미지 (선택)',success)]:
            ttk.Label(win,text=label).grid(row=row,column=0,padx=10,pady=4,sticky='w')
            ttk.Combobox(win,textvariable=var,values=cond_labels,state='readonly',width=45).grid(row=row,column=1,columnspan=3)
        listing=ttk.Treeview(win,columns=('name','verify'),show='headings',height=7)
        listing.heading('name',text='순서대로 클릭할 버튼');listing.heading('verify',text='클릭 후 확인')
        listing.grid(row=3,column=0,columnspan=4,padx=10,pady=8,sticky='ew')
        steps=copy.deepcopy(draft['steps'])
        verify_labels={'disappear':'이 버튼 사라짐','next':'다음 단계 / 최종 이미지 나타남'}
        def redraw():
            listing.delete(*listing.get_children())
            for i,s in enumerate(steps):
                label=next((r['name'] for r in records if r['id']==s['button']),'삭제된 버튼')
                listing.insert('','end',iid=str(i),values=(f'{i+1}. {label}',verify_labels[s['verify']]))
        target=tk.StringVar(value=targets[0]['name'] if targets else '')
        verify=tk.StringVar(value=verify_labels['disappear'])
        ttk.Combobox(win,textvariable=target,values=[r['name'] for r in targets],state='readonly',width=29).grid(row=4,column=0,padx=10)
        ttk.Combobox(win,textvariable=verify,values=list(verify_labels.values()),state='readonly',width=35).grid(row=4,column=1,columnspan=2)
        def append():
            key=next((r['id'] for r in targets if r['name']==target.get()),None)
            if key:
                steps.append({'button':key,'verify':next(k for k,v in verify_labels.items() if v==verify.get())});redraw()
            else:messagebox.showinfo('버튼 등록','먼저 버튼 관리에서 규칙 전용 클릭 이미지를 등록하세요.',parent=win)
        ttk.Button(win,text='단계 추가',command=append).grid(row=4,column=3,padx=10)
        controls=ttk.Frame(win);controls.grid(row=5,column=0,columnspan=4,pady=5)
        def select_step(event):
            if not listing.selection():return
            s=steps[int(listing.selection()[0])]
            target.set(next((r['name'] for r in targets if r['id']==s['button']),''))
            verify.set(verify_labels[s['verify']])
        listing.bind('<<TreeviewSelect>>',select_step)
        def replace_step():
            if not listing.selection():return
            key=next((r['id'] for r in targets if r['name']==target.get()),None)
            if key:
                i=int(listing.selection()[0])
                steps[i]={'button':key,'verify':next(k for k,v in verify_labels.items() if v==verify.get())}
                redraw();listing.selection_set(str(i))
        def change(offset=None):
            if not listing.selection():return
            i=int(listing.selection()[0])
            if offset is None:steps.pop(i);redraw()
            elif 0<=i+offset<len(steps):
                j=i+offset;steps[i],steps[j]=steps[j],steps[i];redraw();listing.selection_set(str(j))
        for text,fn in [('선택 단계 수정',replace_step),('단계 위로',lambda:change(-1)),('단계 아래로',lambda:change(1)),('단계 삭제',change)]:
            ttk.Button(controls,text=text,command=fn).pack(side='left',padx=4)
        once=tk.BooleanVar(value=draft['once'])
        finish=tk.StringVar(value='매크로 중지' if draft['finish']=='stop' else '일반 클릭 재개')
        ttk.Label(win,text='모든 단계 이미지 대기: 시간 제한 없음').grid(row=6,column=0,sticky='w',padx=10)
        ttk.Checkbutton(win,text='실행당 한 번만',variable=once).grid(row=6,column=2,columnspan=2)
        ttk.Label(win,text='완료 후').grid(row=7,column=0,sticky='w',padx=10)
        ttk.Combobox(win,textvariable=finish,values=['매크로 중지','일반 클릭 재개'],state='readonly').grid(row=7,column=1,columnspan=2)
        ttk.Checkbutton(win,text='이 규칙 사용',variable=enabled).grid(row=8,column=0,sticky='w',padx=10)
        ttk.Label(win,text='감지 전용 / 규칙 전용 이미지는 버튼 관리에서 캡처합니다. 사라지지 않는 메뉴는 다음 이미지 확인을 선택하세요.').grid(row=9,column=0,columnspan=4,padx=10,pady=8)
        def save():
            try:
                draft.update(name=name.get().strip(),enabled=enabled.get(),steps=steps,
                    trigger=next((r['id'] for r in conditions if r['name']==trigger.get()),''),
                    success=next((r['id'] for r in conditions if r['name']==success.get()),''),timeout=draft.get('timeout',30.),
                    once=once.get(),finish='stop' if finish.get()=='매크로 중지' else 'resume')
                rules=copy.deepcopy(self.app.rules)
                if adding:rules.append(draft)
                else:rules=[draft if r['id']==draft['id'] else r for r in rules]
                self.commit(rules);win.destroy()
            except Exception as e:messagebox.showerror('규칙 저장 실패',str(e),parent=win)
        ttk.Button(win,text='저장',command=save).grid(row=10,column=2,pady=10)
        ttk.Button(win,text='취소',command=win.destroy).grid(row=10,column=3)
        redraw()
