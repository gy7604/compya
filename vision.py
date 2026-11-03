from pathlib import Path
import threading
import cv2
import numpy as np


_screen_cache=threading.local()


def decode_screen(png,color=False):
    """Share the current immutable screenshot within one worker, never across frames.

    Keep the native gray decoder: converting BGR to gray can change rounding and
    therefore strict price/image comparisons. Only two images are retained.
    """
    flag=cv2.IMREAD_COLOR if color else cv2.IMREAD_GRAYSCALE
    if not isinstance(png,bytes):return cv2.imdecode(np.frombuffer(png,np.uint8),flag)
    if getattr(_screen_cache,'png',None) is not png:
        _screen_cache.png=png;_screen_cache.images={}
    if flag not in _screen_cache.images:
        image=cv2.imdecode(np.frombuffer(png,np.uint8),flag)
        if image is not None:image.flags.writeable=False
        _screen_cache.images[flag]=image
    return _screen_cache.images[flag]


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
        screen = decode_screen(png)
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
        # Non-finite correlations must never become a clickable candidate.
        result = np.where(np.isfinite(result), result, -1).astype(np.float32)
        _, score, _, loc = cv2.minMaxLoc(result)
        patch_gray=region[loc[1]:loc[1]+th,loc[0]:loc[0]+tw]
        contrast_ok=patch_gray.std()>=2
        color_ok=True
        if self.color_tolerance is not None:
            color_screen=decode_screen(png,color=True)
            patch=color_screen[y+loc[1]:y+loc[1]+th,x+loc[0]:x+loc[0]+tw]
            error=float(np.abs(patch.astype(np.float32)-self.color_template.astype(np.float32)).mean())
            color_ok=error<=self.color_tolerance
            score=min(score,1-error/255)
        return bool(contrast_ok and color_ok and score >= self.threshold), (x+loc[0]+tw//2, y+loc[1]+th//2), float(score), (w, h)


class OverlayMatcher(Matcher):
    """Recognize neutral light lettering independently of the moving video background."""
    @staticmethod
    def letters(image):
        return ((image.min(2)>140)&((image.max(2).astype(np.int16)-image.min(2))<30)).astype(np.uint8)*255

    def __init__(self,path,threshold=.68,roi=None):
        super().__init__(path,threshold,roi)
        color=cv2.imdecode(np.fromfile(Path(path),np.uint8),cv2.IMREAD_COLOR)
        self.template=self.letters(color)
        if self.template.std()<2:raise ValueError('영상 글자 형태가 명확하지 않습니다.')

    def find(self,png):
        screen=decode_screen(png,color=True)
        if screen is None:raise ValueError('ADB 화면 PNG 해독 실패')
        h,w=screen.shape[:2];x,y,rw,rh=self.roi or (0,0,w,h)
        if min(x,y)<0 or min(rw,rh)<=0 or x+rw>w or y+rh>h:raise ValueError('검색 영역이 화면을 벗어납니다.')
        region=self.letters(screen[y:y+rh,x:x+rw]);th,tw=self.template.shape
        if th>rh or tw>rw:raise ValueError('템플릿이 검색 영역보다 큽니다.')
        scores=cv2.matchTemplate(region,self.template,cv2.TM_CCOEFF_NORMED)
        scores=np.where(np.isfinite(scores),scores,-1).astype(np.float32)
        _,score,_,loc=cv2.minMaxLoc(scores)
        patch=region[loc[1]:loc[1]+th,loc[0]:loc[0]+tw]
        return bool(patch.std()>=2 and score>=self.threshold),(x+loc[0]+tw//2,y+loc[1]+th//2),float(score),(w,h)


class PriceMatcher(Matcher):
    """Reject even a single changed price digit; broad button correlation is insufficient."""
    def find(self, png):
        found,center,score,size=super().find(png)
        if found:
            screen=decode_screen(png)
            h,w=self.template.shape
            x,y=center[0]-w//2,center[1]-h//2
            patch=screen[y:y+h,x:x+w]
            changed=np.abs(patch.astype(np.int16)-self.template.astype(np.int16))>32
            found=bool(np.mean(changed)<=.001)
        return found,center,score,size
