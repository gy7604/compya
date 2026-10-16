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

