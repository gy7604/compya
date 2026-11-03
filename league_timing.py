"""Local league durations; interrupted and mid-game starts never enter the average."""
import json
import math
from datetime import datetime,timezone
from pathlib import Path


START_NAMES={'nextmatch','한번더하기','경기start','복귀 전용 경기 PLAY BALL'}


def duration_text(seconds):
    minutes,seconds=divmod(max(0,int(seconds)),60)
    return f'{minutes:02d}:{seconds:02d}'


class LeagueTiming:
    limit=10

    def __init__(self,root,dry=False):
        self.path=Path(root)/('league_timing_dry.json' if dry else 'league_timing.json')
        self.samples=[];self.started=None;self.partial=False;self.last=None
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            for sample in data['samples']:
                seconds=sample['seconds']
                if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 30<=seconds<=7200:continue
                self.samples.append(dict(seconds=float(seconds),completed_at=str(sample.get('completed_at',''))))
            self.samples=self.samples[-self.limit:]
        except (OSError,ValueError,KeyError,TypeError):pass

    def start(self,now,partial=False):
        if self.started is not None:return False
        self.started=now;self.partial=partial;self.last=None;return True

    def cancel(self):
        self.started=None;self.partial=False;self.last=None

    def finish(self,now):
        if self.started is None:return None
        seconds=max(0,now-self.started);partial=self.partial
        self.started=None;self.partial=False;self.last=(seconds,partial)
        if not partial and 30<=seconds<=7200:
            self.samples.append(dict(seconds=round(seconds,3),completed_at=datetime.now(timezone.utc).isoformat()))
            self.samples=self.samples[-self.limit:]
            self.save()
        return seconds,partial

    def save(self):
        temp=self.path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(dict(samples=self.samples),ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(self.path)
        except OSError:pass

    def text(self,now=None):
        parts=['리그 시간']
        if self.started is not None and now is not None:
            parts.append('현재 경기 '+duration_text(now-self.started)+(' (경기 도중 측정 시작)' if self.partial else ''))
        elif self.last:
            parts.append('직전 경기 '+duration_text(self.last[0])+(' (일부 구간 · 평균 제외)' if self.last[1] else ''))
        if self.samples:
            mean=sum(s['seconds'] for s in self.samples)/len(self.samples)
            parts.append(f'최근 {len(self.samples)}경기 평균 '+duration_text(round(mean)))
        else:parts.append('완료 경기 평균 측정 대기')
        return ' · '.join(parts)
