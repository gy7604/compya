from engine import SafetyStop


class ActionMachine:
    """Single shared latch: no other action until tapped button disappears."""
    def __init__(self, now, timeout=180, policies=None, priority_order=None, detection_frames=3):
        if type(detection_frames) is not int or not 1 <= detection_frames <= 3:
            raise ValueError('연속 인식 횟수는 1~3입니다.')
        self.timeout = timeout
        self.since = now
        self.pending = None
        self.counts = {}
        self.absent = 0
        self.last_tap = float('-inf')
        self.policies = policies or {}
        self.next_allowed = float('-inf')
        self.priority_order = priority_order
        self.detection_frames = detection_frames

    def observe(self, present, now):
        limit = self.policies.get(self.pending, {}).get('disappear_timeout', 8) if self.pending else self.timeout
        if now-self.since >= limit:
            if self.pending:
                name = self.policies.get(self.pending, {}).get('name', '선택한 버튼')
                raise SafetyStop(f'"{name}" 클릭 후 {limit:g}초 내 사라짐을 확인하지 못했습니다. '
                                 f'(연속 미감지 {self.absent}/3프레임)')
            raise SafetyStop(f'{limit:g}초 동안 사용할 버튼을 찾지 못했습니다. 게임 화면과 사용 설정을 확인하세요.')
        if self.pending:
            self.absent = 0 if present.get(self.pending, False) else self.absent+1
            if self.absent >= 3:
                self.pending = None
                self.since = now
                self.counts.clear()
            return None
        matches = [name for name, visible in present.items() if visible]
        if self.priority_order is not None:
            matches = next(([name] for name in self.priority_order if name in matches), [])
        for name in present:
            self.counts[name] = self.counts.get(name, 0)+1 if matches == [name] else 0
        if len(matches) == 1:
            name = matches[0]
            if self.counts[name] >= self.detection_frames and now >= self.next_allowed:
                self.pending = name
                self.since = self.last_tap = now
                self.absent = 0
                self.next_allowed = now + self.policies.get(name, {}).get('cooldown', 2)
                self.counts.clear()
                return name
        return None
