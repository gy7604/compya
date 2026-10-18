"""Bounded-size visual watchdog; taps alone never reset the timer."""
import cv2
import numpy as np

class StallWatch:
    def __init__(self, seconds=180):
        self.seconds=seconds
        self.reset()

    def reset(self):
        self.reference=None
        self.since=None
        self.last_check=float('-inf')

    def observe(self, png, now):
        if now-self.last_check<2:return False
        self.last_check=now
        frame=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_GRAYSCALE)
        if frame is None:raise RuntimeError('멈춤 감시 화면 PNG 해독 실패')
        small=cv2.resize(frame,(96,160),interpolation=cv2.INTER_AREA).astype(np.int16)
        if self.reference is None or np.mean(np.abs(small-self.reference)>8)>.01:
            self.reference=small
            self.since=now
        return now-self.since>=self.seconds
