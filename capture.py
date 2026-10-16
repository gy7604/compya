"""Pixel-preserving ADB screenshot region registration."""
import base64
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox


def selection_box(start, end, scale, width, height):
    x1, x2 = sorted((max(0, min(width, int(p[0] * scale))) for p in (start, end)))
    y1, y2 = sorted((max(0, min(height, int(p[1] * scale))) for p in (start, end)))
    if x2-x1 < 12 or y2-y1 < 12:
        raise ValueError('버튼 영역을 가로·세로 12픽셀 이상 선택하세요.')
    return x1, y1, x2, y2


def scaled_point(point, scale, width, height):
    x, y = int(point[0]*scale), int(point[1]*scale)
    if scale < 1 or not (0 <= x < width and 0 <= y < height):
        raise ValueError('게임 화면 안의 클릭 위치를 선택하세요.')
    return x, y


def point_editor(parent, png, on_saved):
    previous_grab = parent.grab_current()
    window = tk.Toplevel(parent)
    window.title('클릭 좌표 선택 · 실제 누를 곳을 한 번 클릭하세요')
    window.transient(parent)
    window.grab_set()
    def restore(event):
        if event.widget == window and previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    window.bind('<Destroy>', restore)
    full = tk.PhotoImage(data=base64.b64encode(png).decode('ascii'))
    width, height = full.width(), full.height()
    scale = max(1, math.ceil(width/(parent.winfo_screenwidth()*.75)),
                math.ceil(height/(parent.winfo_screenheight()*.70)))
    preview = full.subsample(scale, scale)
    canvas = tk.Canvas(window, width=preview.width(), height=preview.height(), cursor='crosshair',
                       borderwidth=0, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0,0,anchor='nw',image=preview)
    canvas.preview=preview
    state={'point':None}
    label=tk.StringVar(value=f'게임 원본 화면 {width}×{height} · 누를 곳을 선택하세요. 실제 탭은 보내지 않습니다.')
    def pick(event):
        try:
            state['point']=scaled_point((event.x,event.y),scale,width,height)
            canvas.delete('target')
            canvas.create_oval(event.x-8,event.y-8,event.x+8,event.y+8,outline='#00ff66',width=2,tags='target')
            canvas.create_line(event.x-14,event.y,event.x+14,event.y,fill='#00ff66',tags='target')
            canvas.create_line(event.x,event.y-14,event.x,event.y+14,fill='#00ff66',tags='target')
            label.set(f'지정 좌표 X={state["point"][0]}, Y={state["point"][1]} · 원본 {width}×{height}')
        except Exception as e:
            messagebox.showerror('위치 선택',str(e),parent=window)
    def save():
        if state['point'] is None:
            messagebox.showinfo('위치 선택','먼저 화면에서 실제 누를 곳을 선택하세요.',parent=window)
            return
        on_saved(list(state['point']),[width,height])
        window.destroy()
    canvas.bind('<Button-1>',pick)
    ttk.Label(window,textvariable=label).pack(padx=10,pady=8)
    ttk.Button(window,text='이 좌표 사용',command=save).pack(side='left',padx=10,pady=8)
    ttk.Button(window,text='취소',command=window.destroy).pack(side='right',padx=10,pady=8)


def save_region(png, box, path):
    import cv2
    import numpy as np
    screen = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if screen is None:
        raise ValueError('화면 이미지가 올바르지 않습니다.')
    h, w = screen.shape[:2]
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise ValueError('선택 영역이 화면을 벗어납니다.')
    crop = screen[y1:y2, x1:x2]
    if min(crop.shape[:2]) < 12 or cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std() < 2:
        raise ValueError('선택 영역에 버튼 모양이 충분히 포함되지 않았습니다.')
    path = Path(path)
    path.parent.mkdir(exist_ok=True, parents=True)
    cv2.imencode('.png', crop)[1].tofile(path)
    margin = 25
    left, top = max(0, x1-margin), max(0, y1-margin)
    roi = [left, top, min(w, x2+margin)-left, min(h, y2+margin)-top]
    path.with_suffix('.json').write_text(json.dumps({'size': [w, h], 'roi': roi}), encoding='utf-8')
    return roi


def editor(parent, png, path, on_saved):
    previous_grab = parent.grab_current()
    window = tk.Toplevel(parent)
    window.transient(parent)
    window.grab_set()
    def restore_grab(event):
        if event.widget == window and previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    window.bind('<Destroy>', restore_grab)
    window.title('버튼 등록 · 버튼 전체를 드래그 → 저장')
    full = tk.PhotoImage(data=base64.b64encode(png).decode('ascii'))
    width, height = full.width(), full.height()
    scale = max(1, math.ceil(width / (parent.winfo_screenwidth()*.75)),
                math.ceil(height / (parent.winfo_screenheight()*.70)))
    preview = full.subsample(scale, scale)
    canvas = tk.Canvas(window, width=preview.width(), height=preview.height(), cursor='crosshair')
    canvas.pack()
    canvas.create_image(0, 0, anchor='nw', image=preview)
    canvas.preview = preview
    state = {'start': None, 'end': None, 'rect': None}

    def down(e):
        if state['rect']:
            canvas.delete(state['rect'])
        state['start'] = state['end'] = (e.x, e.y)
        state['rect'] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline='#00ff66', width=2)

    def drag(e):
        if state['start']:
            state['end'] = (e.x, e.y)
            canvas.coords(state['rect'], *state['start'], e.x, e.y)

    def save():
        try:
            if state['start'] is None:
                raise ValueError('먼저 버튼을 마우스로 드래그하여 선택하세요.')
            box = selection_box(state['start'], state['end'], scale, width, height)
            roi = save_region(png, box, path)
            on_saved(str(path), roi)
            window.destroy()
        except Exception as e:
            messagebox.showerror('등록 실패', str(e), parent=window)

    canvas.bind('<Button-1>', down)
    canvas.bind('<B1-Motion>', drag)
    canvas.bind('<ButtonRelease-1>', drag)
    ttk.Label(window, text='버튼 전체를 선택하세요. 원본 해상도로 저장됩니다.').pack()
    ttk.Button(window, text='선택 영역 저장 및 적용', command=save).pack(side='left', padx=10, pady=8)
    ttk.Button(window, text='취소', command=window.destroy).pack(side='right', padx=10)

