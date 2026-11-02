"""Read a changing in-game price only inside a verified currency payment button."""
import re
import subprocess
import tempfile
from pathlib import Path
import cv2
import numpy as np
from engine import SafetyStop


def parse_price(text):
    text=text.strip()
    if not re.fullmatch(r'(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,8})',text):
        raise SafetyStop('갱신 금액을 숫자로 확실히 읽지 못했습니다. 구매하지 않습니다.')
    amount=int(text.replace(',',''))
    if not 1<=amount<=999999999:raise SafetyStop('갱신 금액 범위를 확인할 수 없습니다.')
    return amount


def price_crop(png, icon, point, size):
    image=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
    if image is None or (image.shape[1],image.shape[0])!=tuple(size):
        raise SafetyStop('결제 확인 화면 해상도가 달라 구매하지 않습니다.')
    ix,iy=icon;px,py=point
    if not (px-150<ix<px+130 and abs(iy-py)<35):
        raise SafetyStop('재화 아이콘과 결제 버튼 위치가 달라 구매하지 않습니다.')
    x,y,right,bottom=int(ix+18),int(py-30),int(px+130),int(py+30)
    if not (0<=x<right<=size[0] and 0<=y<bottom<=size[1] and right-x>=40):
        raise SafetyStop('결제 금액 영역이 올바르지 않습니다.')
    return cv2.resize(image[y:bottom,x:right],None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)


class PriceReader:
    def __init__(self, helper):self.helper=Path(helper)

    def read(self, image, folder, variant):
        if not self.helper.is_file():raise SafetyStop('로컬 금액 인식 파일 PriceOCR.exe가 없습니다.')
        if variant:image=255-cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        path=Path(folder)/('price_'+str(variant)+'.png')
        cv2.imencode('.png',image)[1].tofile(path)
        try:
            result=subprocess.run([str(self.helper),str(path.resolve())],capture_output=True,
                                  timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.TimeoutExpired) as error:
            raise SafetyStop('로컬 금액 인식 실패 · 구매하지 않습니다.') from error
        if result.returncode:raise SafetyStop('Windows 금액 인식 사용 불가 · 구매하지 않습니다.')
        return parse_price(result.stdout.decode('utf-8',errors='replace'))

    def verify(self, adb, stop, allowed, key, dialog, other, matchers, record, size):
        readings=[]
        with tempfile.TemporaryDirectory(prefix='compya_price_') as folder:
            for _ in range(3):
                if stop.is_set():return None
                if not allowed():raise SafetyStop('현재 시간에 허용되지 않은 재화입니다. 구매하지 않습니다.')
                png=adb.screenshot()
                if not matchers[dialog].find(png)[0] or (other and matchers[other].find(png)[0]):
                    raise SafetyStop('갱신 팝업/재화가 바뀌어 구매하지 않습니다.')
                found,icon,_,current_size=matchers[key].find(png)
                if not found or tuple(current_size)!=tuple(size):
                    raise SafetyStop('결제 재화가 확실하지 않아 구매하지 않습니다.')
                image=price_crop(png,icon,record['click_point'],size)
                for variant in (0,1):
                    if stop.is_set():return None
                    readings.append(self.read(image,folder,variant))
                if stop.wait(.1):return None
        if len(set(readings))!=1:raise SafetyStop('갱신 금액 판독 결과가 달라 구매하지 않습니다.')
        if not allowed():raise SafetyStop('재화 사용 시간이 지나 구매하지 않습니다.')
        return readings[0]
