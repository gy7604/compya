"""Interrupt a pending gameplay transition only for a known, harmless dialog."""
from engine import SafetyStop


class VideoSkipper:
    """Tap only positively registered cinematic overlays, including during reconnect."""
    def __init__(self, keys, intro_keys=(), end_keys=()):
        self.keys=keys;self.counts={};self.absent=0;self.attempts=0
        self.last=float('-inf');self.hold=False;self.resumed=False;self.pending=False
        self.intro_keys=intro_keys;self.end_keys=end_keys;self.intro=None;self.intro_until=0;self.intro_taps=0;self.chain_used=False
        if intro_keys and not end_keys:raise ValueError('소개 스킵 종료용 PLAY BALL 이미지를 등록하세요.')

    def observe(self, found, now):
        self.resumed=False
        present=any(found.get(k,False) for k in self.keys)
        self.absent=0 if present else self.absent+1
        for key in self.keys:self.counts[key]=self.counts.get(key,0)+1 if found.get(key,False) else 0
        if self.intro:
            if any(found.get(k,False) for k in self.end_keys) or now>=self.intro_until or self.intro_taps>=15:
                self.intro=None;self.resumed=True;self.pending=False;self.hold=False
                return None
            self.hold=True
            if now-self.last>=2:
                self.last=now;self.intro_taps+=1
                return self.intro
            return None
        if self.absent>=3:self.chain_used=False
        self.hold=present and self.attempts<3
        if self.absent>=3:
            self.resumed=self.pending;self.pending=False;self.attempts=0
        if self.hold and now-self.last>=2:
            key=next((k for k in self.keys if self.counts[k]>=2),None)
            if key:
                self.last=now;self.attempts+=1;self.pending=True
                if key in self.intro_keys and not self.chain_used:
                    self.intro=key;self.intro_until=now+45;self.intro_taps=1;self.chain_used=True
                return key
        return None


class CoachAdvice:
    def __init__(self):
        self.visible = self.absent = self.attempts = 0
        self.pending = False
        self.last = float('-inf')
        self.hold = self.resumed = False

    def observe(self, present, now):
        self.resumed = False
        self.visible = self.visible + 1 if present else 0
        self.absent = 0 if present else self.absent + 1
        self.hold = present or self.pending
        if self.pending and self.absent >= 3:
            self.pending = self.hold = False
            self.resumed = True
            self.attempts = 0
        if not present or self.visible < 3 or now - self.last < 8:
            return False
        if self.attempts >= 3:
            raise SafetyStop('코치의 조언 닫기 3회 실패 · 자동 복구 필요')
        self.pending = True
        self.attempts += 1
        self.last = now
        return True
