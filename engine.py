"""Pure, clock-injected pitching state machine (no GUI / ADB dependencies)."""
from dataclasses import dataclass
from enum import Enum
import math


class SafetyStop(RuntimeError):
    pass


class State(Enum):
    READY = '버튼 대기'
    DISAPPEAR = '버튼 사라짐 대기'
    RETURN = '버튼 재등장 대기'


@dataclass(frozen=True)
class Settings:
    visible_frames: int = 3
    absent_frames: int = 3
    cooldown: float = 2.0
    disappear_timeout: float = 8.0
    recognition_timeout: float = 180.0

    def __post_init__(self):
        if min(self.visible_frames, self.absent_frames) < 1:
            raise ValueError('확인 프레임 수는 1 이상이어야 합니다.')
        if any(not math.isfinite(v) or v <= 0 for v in
               (self.cooldown, self.disappear_timeout, self.recognition_timeout)):
            raise ValueError('시간 설정은 양수여야 합니다.')


class PitchMachine:
    def __init__(self, settings, now):
        self.cfg = settings
        self.state = State.READY
        self.since = now
        self.last_tap = float('-inf')
        self.visible = self.absent = self.taps = 0

    def observe(self, present, now):
        """Return True exactly once per confirmed button cycle; latch BEFORE I/O."""
        limit = (self.cfg.disappear_timeout if self.state == State.DISAPPEAR
                 else self.cfg.recognition_timeout)
        if now - self.since >= limit:
            raise SafetyStop(f'{self.state.value}: {limit:g}초 시간 초과')
        self.visible = self.visible + 1 if present else 0
        self.absent = self.absent + 1 if not present else 0
        if self.state == State.DISAPPEAR:
            if self.absent >= self.cfg.absent_frames:
                self.state = State.RETURN
                self.since = now
                self.visible = self.absent = 0
            return False
        if self.visible >= self.cfg.visible_frames and now - self.last_tap >= self.cfg.cooldown:
            self.state = State.DISAPPEAR
            self.since = self.last_tap = now
            self.visible = self.absent = 0
            self.taps += 1
            return True
        return False
