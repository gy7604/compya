"""Read the baseball stock beside its icon, only on registered lobby screens."""
import re
import subprocess
import tempfile
from pathlib import Path
import cv2
import numpy as np
from vision import Matcher,decode_screen

FILES=('ball_balance_icon.png',)


def parse_stock(text):
    match=re.fullmatch(r'\s*([0-9]{1,4})(?:\s*/\s*|\s+)([0-9]{2,4})\s*',text)
    if not match:return None
    have,capacity=map(int,match.groups())
    # The denominator is storage capacity, never the five-ball match cost.
    return (have,capacity) if 50<=capacity<=9999 else None


def separate_stock(image):
    # The outlined slash joins the following 1 in Windows OCR. Locate its
    # descending diagonal and replace only that separator with blank space.
    binary=(image.min(2)>180).astype(np.uint8)
    _,labels,stats,_=cv2.connectedComponentsWithStats(binary)
    parts=[]
    for index,(x,y,w,h,area) in enumerate(stats[1:],1):
        if not (60<=x<=75 and 2<=w<=8 and h>=4 and area>=6):continue
        yy,xx=np.where(labels==index)
        correlation=np.corrcoef(xx,yy)[0,1] if xx.std() and yy.std() else 0
        if correlation<-.25:parts.append(index)
    if not parts:return None
    yy,xx=np.where(np.isin(labels,parts))
    if not (4<=np.ptp(xx)+1<=9 and 13<=np.ptp(yy)+1<=23 and np.corrcoef(xx,yy)[0,1]<-.8):return None
    left,right=int(xx.min()),int(xx.max()+1)
    gap=np.full((image.shape[0],20,3),image[0,0],np.uint8)
    image=np.concatenate((image[:,:left],gap,image[:,right:]),axis=1)
    return cv2.copyMakeBorder(image,10,10,15,15,cv2.BORDER_CONSTANT,value=tuple(map(int,image[0,0])))


class BallBalanceReader:
    def __init__(self,root):
        self.root=Path(root);self.icon=None;self.contexts=[]
        path=self.root/'assets'/FILES[0]
        if not path.is_file():return
        self.icon=Matcher(path,.94,[485,72,70,65])
        groups=[(('league_next_match.png',[190,550,350,110]),),
                (('sponsor_home_tab.png',[170,1065,140,85]),('sponsor_home_play.png',[310,1065,225,90])),
                (('sponsor_title.png',[150,145,420,115]),('sponsor_settle.png',[190,1090,340,100]))]
        for group in groups:
            if all((self.root/'assets'/name).is_file() for name,_ in group):
                self.contexts.append([Matcher(self.root/'assets'/name,.91,roi) for name,roi in group])

    def visible(self,png):
        if self.icon is None or not isinstance(png,bytes):return False
        image=decode_screen(png)
        return bool(image is not None and image.shape==(1280,720) and self.icon.find(png)[0]
                    and any(all(m.find(png)[0] for m in group) for group in self.contexts))

    def ocr(self,image,path,variant):
        if variant:image=255-cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        cv2.imencode('.png',image)[1].tofile(path)
        result=subprocess.run([str(self.root/'PriceOCR.exe'),str(path)],capture_output=True,timeout=8,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return parse_stock(result.stdout.decode('utf-8').strip()) if result.returncode==0 else None

    def read(self,png,stop):
        if stop.is_set() or not self.visible(png):return None
        image=decode_screen(png,True)[77:125,563:671]
        image=separate_stock(image)
        if image is None:return None
        image=cv2.resize(image,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
        try:
            with tempfile.TemporaryDirectory(prefix='compya_stock_') as folder:
                values=[]
                for variant in (0,1):
                    if stop.is_set():return None
                    values.append(self.ocr(image,Path(folder)/f'stock_{variant}.png',variant))
                return values[0] if not stop.is_set() and values[0] is not None and values[0]==values[1] else None
        except (OSError,UnicodeError,ValueError,subprocess.TimeoutExpired,cv2.error):return None
