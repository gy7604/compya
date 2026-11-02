"""Interrupt a pending gameplay transition only for a known, harmless dialog."""
from engine import SafetyStop


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
