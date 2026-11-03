"""Local result OCR and daily summaries. Never infer five games from a batch."""
import re
import subprocess
import tempfile
from pathlib import Path
import cv2
from vision import decode_screen


def parse_record(text):
    # Windows OCR can reorder words in a visual row. Identify each explicit
    # label independently, rejecting conflicting totals and words like 승리.
    matches=re.findall(r'(?<![0-9+\-])([0-9]{1,2})\s*([승무패])(?![가-힣])',text)
    values={label:{int(n) for n,key in matches if key==label} for label in '승무패'}
    if any(len(numbers)!=1 for numbers in values.values()):raise ValueError('승무패 합계 판독 불확실')
    result=tuple(values[label].pop() for label in '승무패')
    if not 1<=sum(result)<=5:raise ValueError('연속 경기 결과 범위 불확실')
    return result


def summary_text(day,entry):
    results=entry.get('results',{});wins=draws=losses=known=0
    for batch in range(1,entry.get('completed',0)+1):
        value=results.get(str(batch))
        if not isinstance(value,list) or len(value)!=3 or any(type(n) is not int or n<0 for n in value) or not 1<=sum(value)<=5:continue
        wins+=value[0];draws+=value[1];losses+=value[2];known+=1
    missing=entry.get('completed',0)-known
    lines=[f'랭킹챌린지 하루 일정 완료 · {day}']
    if missing and not known:
        lines += ['당일 경기 수/승무패: 판독 확인 필요',f'미집계 결과: {missing}묶음']
    elif missing:
        lines += [f'확인된 경기: {wins+draws+losses}경기',f'확인된 전적: {wins}승 {draws}무 {losses}패',
                  f'미집계 결과: {missing}묶음 · 전체 경기 수/전적은 확인 필요']
    else:lines += [f'당일 총 경기: {wins+draws+losses}경기',f'전적: {wins}승 {draws}무 {losses}패']
    lines.append('집계 범위: 프로그램에서 확인한 경기')
    if entry.get('rewards_claimed'):lines.append('달성 보상 수령 완료')
    lines.append('리그모드로 복귀 진행')
    return '\n'.join(lines)


class RankingResultReader:
    def __init__(self,root):self.root=Path(root);self.helper=self.root/'PriceOCR.exe'

    def confirm_visible(self,adb,stop,result_matcher,confirm_matcher):
        if stop.is_set():return False
        try:
            png=adb.screenshot()
            return all(found and size==(720,1280) for found,point,score,size in
                       (result_matcher.find(png),confirm_matcher.find(png))) and not stop.is_set()
        except (OSError,RuntimeError,ValueError,cv2.error):return False

    def read(self,image,folder,variant):
        if not self.helper.is_file():raise ValueError('로컬 결과 인식 파일 없음')
        if variant:image=255-cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        path=Path(folder)/f'result_{variant}.png';cv2.imencode('.png',image)[1].tofile(path)
        result=subprocess.run([str(self.helper),str(path.resolve()),'ko'],capture_output=True,timeout=8,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('한국어 결과 인식 사용 불가')
        return parse_record(result.stdout.decode('utf-8',errors='replace'))

    def verify(self,png,adb,stop,result_matcher,confirm_matcher):
        """Require the result screen on two frames, with two matching OCR variants."""
        try:
            readings=[]
            with tempfile.TemporaryDirectory(prefix='compya_result_') as folder:
                for index in range(2):
                    if stop.is_set():return None
                    if index:png=adb.screenshot()
                    title,confirm=result_matcher.find(png),confirm_matcher.find(png)
                    if not title[0] or not confirm[0] or title[3]!=(720,1280) or confirm[3]!=(720,1280):return None
                    image=cv2.resize(decode_screen(png,True),None,fx=1.5,fy=1.5,interpolation=cv2.INTER_CUBIC)
                    for variant in (0,1):
                        if stop.is_set():return None
                        readings.append(self.read(image,folder,variant))
            if stop.is_set() or len(set(readings))!=1:return None
            return readings[0]
        except (OSError,ValueError,subprocess.TimeoutExpired,RuntimeError,cv2.error):return None

    def retain_unreadable(self,png,day,batch):
        try:
            folder=self.root/'logs'/'ranking_results'/day;folder.mkdir(parents=True,exist_ok=True)
            (folder/f'batch_{batch}.png').write_bytes(png)
        except OSError:pass
