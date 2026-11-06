"""Recognize the stable coach title AND cancel control, not variable advice text."""
from pathlib import Path
from vision import Matcher,decode_screen

FILES=('coach_title_v2.png','coach_cancel_v2.png')


class CoachDialog:
    def __init__(self,root):
        self.title=Matcher(Path(root)/'assets'/FILES[0],.94,[265,120,190,65])
        self.cancel=Matcher(Path(root)/'assets'/FILES[1],.94,[365,255,180,80])

    def find(self,png):
        image=decode_screen(png)
        if image is None:raise ValueError('코치 안내 화면 PNG 확인 실패')
        if image.shape!=(1280,720):return False,None,0.,(image.shape[1],image.shape[0])
        title=self.title.find(png);cancel=self.cancel.find(png)
        size=title[3]
        return title[0] and cancel[0] and size==(720,1280),cancel[1],min(title[2],cancel[2]),size
