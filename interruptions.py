"""Interrupt a pending gameplay transition only for a known, harmless dialog."""
from engine import SafetyStop


class PopupCloser:
    """Give registered harmless popups precedence over every mode supervisor."""
    def __init__(self,keys):
        self.keys=list(keys);self.counts={};self.pending=None;self.absent=0;self.attempts=0
        self.last=float('-inf');self.hold=False;self.resumed=False;self.events=[]
        self.appearance=None;self.candidate=None;self.scene_frames=0;self.scene_since=None
        self.total=0;self.chain_since=None;self.clear_frames=0;self.changed=0

    def observe(self,found,now,appearances=None):
        self.resumed=False
        for key in self.keys:self.counts[key]=self.counts.get(key,0)+1 if found.get(key,False) else 0
        present=next((key for key in self.keys if found.get(key,False)),None)
        self.clear_frames=0 if present else self.clear_frames+1
        if present and self.chain_since is None:self.chain_since=now
        self.hold=bool(self.pending or present)
        if self.pending:
            self.absent=0 if found.get(self.pending,False) else self.absent+1
            if self.absent>=3:
                self.pending=None;self.attempts=0;self.absent=0;self.resumed=True
                self.appearance=None;self.candidate=None;self.scene_frames=0;self.scene_since=None
                self.hold=bool(present)
                if self.clear_frames>=3:self.total=0;self.chain_since=None;self.changed=0
                self.events.append('공지/광고 닫힘 확인 · 현재 화면부터 진행 재개')
                return None
            if not found.get(self.pending,False):return None
            key=self.pending
        else:key=present
        if not self.hold and self.clear_frames>=3:self.total=0;self.chain_since=None;self.changed=0
        evidence=(appearances or {}).get(key)
        scene_wait=False
        if self.pending and self.appearance and evidence and evidence.different_from(self.appearance):
            if self.candidate and evidence.stable_with(self.candidate):self.scene_frames+=1
            else:self.candidate=evidence;self.scene_frames=1;self.scene_since=now
            if self.scene_frames>=3 and now-self.scene_since>=.4 and now-self.last>=1:
                self.appearance=evidence;self.attempts=0;self.changed+=1
                self.candidate=None;self.scene_frames=0;self.scene_since=None
                self.events.append('연속 광고 변경 확인 · 새 광고의 닫기 횟수 분리 · 전체 한도 유지')
            else:scene_wait=True
        else:self.candidate=None;self.scene_frames=0;self.scene_since=None
        if self.hold and self.chain_since is not None and now-self.chain_since>=180:
            raise SafetyStop('연속 공지/광고 처리 180초 초과 · 추가 클릭 없이 확인 필요')
        if scene_wait:return None
        if key is None or self.counts[key]<3 or now-self.last<8:return None
        if self.total>=12:raise SafetyStop('연속 공지/광고 닫기 전체 12회 한도 · 추가 클릭 없이 확인 필요')
        if self.attempts>=3:raise SafetyStop('등록된 공지/광고 닫기 3회 후에도 표시 · 추가 클릭 없이 확인 필요')
        self.pending=key;self.attempts+=1;self.total+=1;self.last=now
        if evidence:self.appearance=evidence
        return key

    def diagnostics(self):
        return dict(pending=self.pending,attempts=self.attempts,total=self.total,changed=self.changed,
                    absent=self.absent,appearance_verified=self.appearance is not None)


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
            if any(found.get(k,False) for k in self.end_keys) or now>=self.intro_until or self.intro_taps>=30:
                self.intro=None;self.resumed=True;self.pending=False;self.hold=False
                return None
            self.hold=True
            if now-self.last>=1:
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
