import base64
import copy
import json
import shutil
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from registry import new_button, validate
from clicks import click_point


class ButtonManager:
    def __init__(self, app):
        self.app = app
        self.store = app.registry
        self.window = tk.Toplevel(app.root)
        self.window.title('버튼 관리 · 추가 / 수정 / 이미지 교체')
        self.window.geometry('1060x500')
        self.window.transient(app.root)
        self.window.grab_set()
        self.tree = ttk.Treeview(self.window, columns=('priority', 'enabled', 'kind', 'name', 'threshold', 'image'), show='headings')
        for key, label, width in [('priority', '우선', 45), ('enabled', '사용', 55), ('kind', '용도', 100), ('name', '버튼/이미지 이름', 170),
                                  ('threshold', '인식 기준', 80), ('image', '등록 이미지', 420)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width)
        self.tree.pack(fill='both', expand=True, padx=10, pady=10)
        self.tree.bind('<Double-1>', lambda e: self.edit())
        bar = ttk.Frame(self.window)
        bar.pack(fill='x', padx=10)
        for label, fn in [('버튼 추가', lambda: self.edit(True)), ('선택 수정 / 재캡처', self.edit),
                          ('사용 / 해제', self.toggle), ('위로 ↑', lambda: self.move(-1)),
                          ('아래로 ↓', lambda: self.move(1)), ('선택 삭제', self.delete)]:
            ttk.Button(bar, text=label, command=fn).pack(side='left', padx=3)
        ttk.Label(self.window, text='동시에 보이는 일반 클릭 버튼은 위에 있는 것부터 처리합니다. 감지 전용 이미지는 클릭하지 않습니다.').pack(pady=8)
        ttk.Button(self.window, text='닫기', command=self.window.destroy).pack(pady=5)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        kinds = {'normal': '일반 클릭', 'condition': '감지 전용', 'step': '규칙 전용 클릭'}
        for index, r in enumerate(self.app.records, 1):
            self.tree.insert('', 'end', iid=r['id'], values=(
                index, 'ON' if r['enabled'] else 'OFF', kinds[r.get('kind', 'normal')], r['name'], r['threshold'], r['template'] or '미등록'))
        self.app.button_summary.set(f'등록 {len(self.app.records)}개 / 사용 {sum(r["enabled"] for r in self.app.records)}개')

    def selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo('버튼 선택', '목록에서 버튼을 선택하세요.', parent=self.window)
            return None
        return next(r for r in self.app.records if r['id'] == selected[0])

    def commit(self, records):
        self.store.save(records)
        self.app.records = records
        self.refresh()

    def toggle(self):
        r = self.selected()
        if r is None:
            return
        records = copy.deepcopy(self.app.records)
        next(x for x in records if x['id'] == r['id'])['enabled'] = not r['enabled']
        try:
            self.commit(records)
        except Exception as e:
            messagebox.showerror('저장 실패', str(e), parent=self.window)

    def move(self, offset):
        r = self.selected()
        if r is None:
            return
        records = copy.deepcopy(self.app.records)
        index = next(i for i, item in enumerate(records) if item['id'] == r['id'])
        target = index+offset
        if not 0 <= target < len(records):
            return
        records[index], records[target] = records[target], records[index]
        try:
            self.commit(records)
            self.tree.selection_set(r['id'])
            self.tree.see(r['id'])
        except Exception as e:
            messagebox.showerror('저장 실패', str(e), parent=self.window)

    def delete(self):
        r = self.selected()
        if r is not None and messagebox.askyesno('버튼 삭제', f'{r["name"]} 버튼을 목록에서 삭제할까요?', parent=self.window):
            try:
                self.commit([x for x in self.app.records if x['id'] != r['id']])
            except Exception as e:
                messagebox.showerror('삭제 실패', str(e), parent=self.window)

    def edit(self, adding=False):
        original = new_button() if adding else self.selected()
        if original is None:
            return
        draft = copy.deepcopy(original)
        win = tk.Toplevel(self.window)
        win.title('버튼 추가' if adding else '버튼 수정 · 업데이트 후 이미지를 교체하세요')
        win.transient(self.window)
        win.grab_set()
        def restore_manager(event):
            if event.widget == win and self.window.winfo_exists():
                self.window.grab_set()
        win.bind('<Destroy>', restore_manager)
        values = {}
        labels = [('name', '버튼 이름'), ('threshold', '인식 기준 (0.5~1)'),
                  ('cooldown', '클릭 후 최소 간격 (초)'), ('click_delay', '클릭 전 대기 (초 · 일반/조건 규칙)'),
                  ('retry_interval','자동 재클릭 간격 (초 · 일반 버튼)'), ('retry_limit','자동 재클릭 추가 횟수 (0=OFF · 일반 버튼)'),
                  ('disappear_frames','사라짐 확인 횟수 (1~3 · 일반 버튼)'), ('roi', '검색 영역 x,y,너비,높이 (빈칸=전체)')]
        for row, (key, label) in enumerate(labels):
            v = draft.get(key,{'click_delay':0,'retry_interval':8,'retry_limit':0,'disappear_frames':3}.get(key))
            if key == 'roi':
                v = ','.join(map(str, v)) if v else ''
            values[key] = tk.StringVar(value=str(v))
            ttk.Label(win, text=label).grid(row=row, column=0, padx=10, pady=5, sticky='w')
            ttk.Entry(win, textvariable=values[key], width=48).grid(row=row, column=1, columnspan=2, padx=10)
        enabled = tk.BooleanVar(value=draft['enabled'])
        ttk.Checkbutton(win, text='이 이미지 사용', variable=enabled).grid(row=8, column=0, sticky='w', padx=10)
        kind_labels = {'normal': '일반 클릭', 'condition': '감지 전용 (클릭 안 함)', 'step': '규칙 전용 클릭'}
        kind = tk.StringVar(value=kind_labels[draft.get('kind', 'normal')])
        ttk.Combobox(win, textvariable=kind, values=list(kind_labels.values()), state='readonly', width=35).grid(row=8, column=1, columnspan=2)
        image_label = tk.StringVar(value=draft['template'] or '이미지 미등록')
        ttk.Label(win, textvariable=image_label, wraplength=520).grid(row=9, column=0, columnspan=3, padx=10, pady=8)
        click_labels={'center':'인식한 이미지 중앙','fixed':'지정 좌표'}
        click_mode=tk.StringVar(value=click_labels[draft.get('click_mode','center')])
        saved_point=draft.get('click_point') or ['', '']
        click_x=tk.StringVar(value=str(saved_point[0]));click_y=tk.StringVar(value=str(saved_point[1]))
        click_info=tk.StringVar(value=f'기준 해상도: {draft.get("click_size") or draft.get("size") or "미등록"}')

        def click_fields():
            mode=next(key for key,label in click_labels.items() if label==click_mode.get())
            if mode=='center':return dict(click_mode='center',click_point=None,click_size=None)
            try:point=[int(click_x.get()),int(click_y.get())]
            except ValueError:raise ValueError('클릭 좌표 X,Y를 정수로 입력하거나 화면에서 선택하세요.')
            return dict(click_mode='fixed',click_point=point,click_size=draft.get('click_size') or draft.get('size'))

        def pick_point():
            def received(png):
                if not win.winfo_exists():return
                from capture import point_editor
                def saved(point,size):
                    draft['click_size']=size
                    click_x.set(str(point[0]));click_y.set(str(point[1]))
                    click_mode.set(click_labels['fixed'])
                    click_info.set(f'기준 해상도: {size[0]}×{size[1]}')
                point_editor(win,png,saved)
            self.app.request_capture(received)

        def accept_image(path, roi=None, size=None):
            draft['template'] = self.store.portable(path)
            draft['size'] = size
            values['roi'].set(','.join(map(str, roi)) if roi else '')
            image_label.set(draft['template'])

        def destination():
            return self.store.root/'assets'/'buttons'/f'{draft["id"]}_{time.time_ns()}.png'

        def capture():
            path = destination()
            def received(png):
                if not win.winfo_exists():
                    return
                from capture import editor
                def saved(saved_path, roi):
                    size = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['size']
                    accept_image(saved_path, roi, size)
                    win.grab_set()
                editor(win, png, path, saved)
            self.app.request_capture(received)

        def import_image():
            path = filedialog.askopenfilename(parent=win, filetypes=[('버튼 PNG 이미지', '*.png')])
            if not path:
                return
            try:
                from vision import Matcher
                Matcher(path)
                dst = destination()
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
                accept_image(dst)
            except Exception as e:
                messagebox.showerror('이미지 불러오기 실패', str(e), parent=win)

        def preview():
            try:
                path = self.store.resolve(draft['template'])
                image = tk.PhotoImage(data=base64.b64encode(path.read_bytes()).decode('ascii'))
                popup = tk.Toplevel(win)
                popup.title('현재 등록 이미지')
                popup.transient(win)
                factor = max(1, (image.width()+499)//500, (image.height()+499)//500)
                image = image.subsample(factor, factor)
                label = ttk.Label(popup, image=image)
                label.image = image
                label.pack(padx=10, pady=10)
            except Exception as e:
                messagebox.showerror('미리보기 실패', str(e), parent=win)

        def test():
            try:
                from vision import Matcher
                roi = [int(x) for x in values['roi'].get().split(',')] if values['roi'].get().strip() else None
                matcher = Matcher(self.store.resolve(draft['template']), float(values['threshold'].get()), roi)
                def received(png):
                    if not win.winfo_exists():
                        return
                    try:
                        found, point, score, size = matcher.find(png)
                        if draft.get('size') and tuple(draft['size']) != size:
                            raise ValueError('등록 해상도와 현재 화면이 다릅니다. 다시 캡처하세요.')
                        click_record=dict(draft,**click_fields())
                        target=click_point(click_record,point,size)
                        messagebox.showinfo('인식 테스트 · 클릭 없음',
                            f'{"인식 성공" if found else "인식 실패"}\n점수: {score:.3f}\n인식 이미지 중앙: {point}\n클릭 예정 좌표: {target}\n화면: {size}\n감지 전용은 클릭하지 않습니다.', parent=win)
                    except Exception as e:
                        messagebox.showerror('인식 테스트 실패', str(e), parent=win)
                self.app.request_capture(received)
            except Exception as e:
                messagebox.showerror('인식 테스트 실패', str(e), parent=win)

        def apply():
            if self.app.capturing:
                messagebox.showinfo('캡처 중', '화면 캡처가 끝난 뒤 저장하세요.', parent=win)
                return
            try:
                draft['name'] = values['name'].get().strip()
                for key in ('threshold', 'cooldown', 'click_delay', 'retry_interval'):
                    draft[key] = float(values[key].get())
                draft['enabled'] = enabled.get()
                draft['kind'] = next(key for key, label in kind_labels.items() if label == kind.get())
                for key in ('retry_limit','disappear_frames'):draft[key]=int(values[key].get())
                draft['roi'] = [int(x) for x in values['roi'].get().split(',')] if values['roi'].get().strip() else None
                draft.update(click_fields())
                records = copy.deepcopy(self.app.records)
                if adding:
                    records.append(draft)
                else:
                    records = [draft if x['id'] == draft['id'] else x for x in records]
                validate(records)
                if draft['template']:
                    from vision import Matcher
                    Matcher(self.store.resolve(draft['template']), draft['threshold'], draft['roi'])
                self.commit(records)
                win.destroy()
                self.window.grab_set()
            except Exception as e:
                messagebox.showerror('저장 실패', str(e), parent=win)

        ttk.Button(win, text='게임 화면 캡처 / 이미지 교체', command=capture).grid(row=10, column=0, padx=10, pady=10)
        ttk.Button(win, text='PNG 불러오기', command=import_image).grid(row=10, column=1)
        ttk.Button(win, text='이미지 보기', command=preview).grid(row=10, column=2, padx=10)
        ttk.Button(win, text='현재 화면 인식 테스트 (클릭 없음)', command=test).grid(row=11, column=0, columnspan=3, pady=5)
        point_bar=ttk.Frame(win);point_bar.grid(row=12,column=0,columnspan=3,padx=10,pady=6,sticky='w')
        ttk.Label(point_bar,text='클릭 위치 ').pack(side='left')
        ttk.Combobox(point_bar,textvariable=click_mode,values=list(click_labels.values()),state='readonly',width=23).pack(side='left')
        ttk.Label(point_bar,text=' X ').pack(side='left');ttk.Entry(point_bar,textvariable=click_x,width=6).pack(side='left')
        ttk.Label(point_bar,text=' Y ').pack(side='left');ttk.Entry(point_bar,textvariable=click_y,width=6).pack(side='left')
        ttk.Button(win,text='화면에서 클릭 위치 선택',command=pick_point).grid(row=13,column=0,padx=10,pady=5)
        ttk.Label(win,textvariable=click_info).grid(row=13,column=1,columnspan=2,sticky='w')
        ttk.Label(win, text='이미지가 인식되면 위 좌표로 탭을 보냅니다. 저장을 눌러야 적용됩니다.').grid(row=14, column=0, columnspan=3)
        ttk.Button(win, text='저장', command=apply).grid(row=15, column=1, pady=12)
        ttk.Button(win, text='취소', command=win.destroy).grid(row=15, column=2)
