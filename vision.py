from pathlib import Path
import cv2
import numpy as np


class Matcher:
    def __init__(self, path, threshold=0.90, roi=None, color_tolerance=None):
        if not 0.5 <= threshold <= 1:
            raise ValueError('매칭 임계값은 0.5~1 범위입니다.')
        self.template = cv2.imdecode(np.fromfile(Path(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if self.template is None or min(self.template.shape) < 8 or self.template.std() < 2:
            raise ValueError('템플릿 이미지가 없거나 유효하지 않습니다.')
        self.threshold, self.roi = threshold, roi
        self.color_tolerance=color_tolerance
        self.color_template=cv2.imdecode(np.fromfile(Path(path),dtype=np.uint8),cv2.IMREAD_COLOR) if color_tolerance is not None else None

    def find(self, png):
        screen = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError('ADB 화면 PNG 해독 실패')
        h, w = screen.shape
        x, y, rw, rh = self.roi or (0, 0, w, h)
        if min(x, y) < 0 or min(rw, rh) <= 0 or x + rw > w or y + rh > h:
            raise ValueError('검색 영역이 화면을 벗어납니다.')
        region = screen[y:y+rh, x:x+rw]
        th, tw = self.template.shape
        if th > rh or tw > rw:
            raise ValueError('템플릿이 검색 영역보다 큽니다.')
        result = cv2.matchTemplate(region, self.template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        color_ok=True
        if self.color_tolerance is not None:
            color_screen=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
            patch=color_screen[y+loc[1]:y+loc[1]+th,x+loc[0]:x+loc[0]+tw]
            error=float(np.abs(patch.astype(np.float32)-self.color_template.astype(np.float32)).mean())
            color_ok=error<=self.color_tolerance
            score=min(score,1-error/255)
        return color_ok and score >= self.threshold, (x+loc[0]+tw//2, y+loc[1]+th//2), score, (w, h)
