from engine import SafetyStop


class ActionMachine:
    """Single shared latch: no other action until tapped button disappears."""
    def __init__(self, now, timeout=180, policies=None):
        self.timeout = timeout
        self.since = now
        self.pending = None
        self.counts = {}
        self.absent = 0
        self.last_tap = float('-inf')
        self.policies = policies or {}
        self.next_allowed = float('-inf')

    def observe(self, present, now):
        limit = self.policies.get(self.pending, {}).get('disappear_timeout', 8) if self.pending else self.timeout
        if now-self.since >= limit:
            raise SafetyStop(f'{self.pending or "버튼 인식"} 대기 시간 초과')
        if self.pending:
            self.absent = 0 if present.get(self.pending, False) else self.absent+1
            if self.absent >= 3:
                self.pending = None
                self.since = now
                self.counts.clear()
            return None
        # Ambiguous simultaneous matches never trigger a click.
        matches = [name for name, visible in present.items() if visible]
        for name in present:
            self.counts[name] = self.counts.get(name, 0)+1 if matches == [name] else 0
        if len(matches) == 1:
            name = matches[0]
            if self.counts[name] >= 3 and now >= self.next_allowed:
                self.pending = name
                self.since = self.last_tap = now
                self.absent = 0
                self.next_allowed = now + self.policies.get(name, {}).get('cooldown', 2)
                self.counts.clear()
                return name
        return None
