class ActionMachine:
    """Indefinite normal-button wait; latch prevents repeats until disappearance."""
    def __init__(self, now, timeout=None, policies=None, priority_order=None, detection_frames=3):
        if type(detection_frames) is not int or not 1 <= detection_frames <= 3:
            raise ValueError('연속 인식 횟수는 1~3입니다.')
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
