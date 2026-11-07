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
    if not re.fullmatch(r'(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,2}(?: [0-9]{3})+|[1-9][0-9]{0,8})',text):
        raise SafetyStop('갱신 금액을 숫자로 확실히 읽지 못했습니다. 구매하지 않습니다.')
    amount=int(text.replace(',','').replace(' ',''))
    if not 1<=amount<=999999999:raise SafetyStop('갱신 금액 범위를 확인할 수 없습니다.')
    return amount


def parse_payment(text):
    match=re.fullmatch(r'(.+?)\s+([0-9]{1,3})/([1-9][0-9]{0,2})',text.strip())
    if not match:raise SafetyStop('갱신 금액/남은 횟수를 확실히 읽지 못했습니다.')
    amount=parse_price(match[1]);remaining,total=int(match[2]),int(match[3])
    if not 0<=remaining<=total<=999:raise SafetyStop('게임의 남은 갱신 횟수가 올바르지 않습니다.')
    return amount,remaining,total


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


def payment_row(price,count):
    """Keep the actual digits, align their height, and read them on one line.

    Windows OCR can drop a short price such as 80 when a separate, differently
    colored count line is present. No digits or currency labels are inserted.
    """
    mask=(price.min(axis=2)>150)&((price.max(axis=2).astype(np.int16)-price.min(axis=2))<90)
    if not mask.any() or mask.all():raise SafetyStop('갱신 금액의 숫자 형태를 확인하지 못했습니다.')
    price=np.repeat(np.where(mask,0,255).astype(np.uint8)[:,:,None],3,axis=2)
    tiles=[]
    for image,pad in ((price,8),(count,5)):
        ys,xs=np.where(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)<150)
        if len(xs)<8:raise SafetyStop('갱신 금액/횟수의 숫자 형태를 확인하지 못했습니다.')
        image=image[max(0,int(ys.min())-pad):int(ys.max())+pad+1,
                    max(0,int(xs.min())-pad):int(xs.max())+pad+1]
        tiles.append(cv2.resize(image,(max(1,round(image.shape[1]*120/image.shape[0])),120),interpolation=cv2.INTER_CUBIC))
    price,count=tiles
    row=np.full((160,price.shape[1]+count.shape[1]+100,3),255,np.uint8)
    row[20:140,20:20+price.shape[1]]=price
    row[20:140,price.shape[1]+70:price.shape[1]+70+count.shape[1]]=count
    return row


class PriceReader:
    def __init__(self, helper):self.helper=Path(helper)

    def read(self, image, folder, variant, with_count=False):
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
        text=result.stdout.decode('utf-8',errors='replace')
        return parse_payment(text) if with_count else parse_price(text)

    def verify(self, adb, stop, allowed, key, dialog, other, matchers, record, size,with_count=False):
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
                if with_count:
                    frame=cv2.imdecode(np.frombuffer(png,np.uint8),cv2.IMREAD_COLOR)
                    px,py=record['click_point']
                    x,y,right,bottom=px+38,py-174,px+109,py-146
                    if not (0<=x<right<=size[0] and 0<=y<bottom<=size[1]):
                        raise SafetyStop('게임의 남은 횟수 영역이 올바르지 않습니다.')
                    count=cv2.resize(frame[y:bottom,x:right],None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
                    image=payment_row(image,count)
                for variant in (0,1):
                    if stop.is_set():return None
                    readings.append(self.read(image,folder,variant,with_count))
                if stop.wait(.1):return None
        if len(set(readings))!=1:raise SafetyStop('갱신 금액 판독 결과가 달라 구매하지 않습니다.')
        if not allowed():raise SafetyStop('재화 사용 시간이 지나 구매하지 않습니다.')
        return readings[0]
