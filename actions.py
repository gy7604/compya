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
        self.candidate=None
        self.candidate_since=None
        self.observed_now=now
        self.retries=0;self.retry_visible=0;self.retry_event=None

    @property
    def delay_remaining(self):
        if self.candidate is None:return 0.
        return max(0.,float(self.policies.get(self.candidate,{}).get('click_delay',0))-(self.observed_now-self.candidate_since))

    def observe(self, present, now):
        self.observed_now=now
        self.retry_event=None
        if self.pending:
            policy=self.policies.get(self.pending,{})
            visible=present.get(self.pending,False)
            self.absent = 0 if visible else self.absent+1
            self.retry_visible=self.retry_visible+1 if visible else 0
            required=policy.get('disappear_frames',3)
            if self.absent >= required:
                self.pending = None
                self.since = now
                self.counts.clear()
                self.retries=0;self.retry_visible=0
            elif (visible and self.retry_visible>=3 and self.retries<policy.get('retry_limit',0)
                  and not any(value for key,value in present.items() if key!=self.pending)
                  and now-self.last_tap>=max(float(policy.get('retry_interval',8)),float(policy.get('click_delay',0)))
                  and now>=self.next_allowed):
                self.retries+=1;self.last_tap=now;self.retry_visible=0
                self.next_allowed=now+policy.get('cooldown',2)
                self.retry_event=f'자동 재클릭 · {self.pending} · {self.retries}/{policy.get("retry_limit",0)}회'
                return self.pending
            return None
        matches = [name for name, visible in present.items() if visible]
        if self.priority_order is not None:
            matches = next(([name] for name in self.priority_order if name in matches), [])
        chosen=matches[0] if len(matches)==1 else None
        if chosen!=self.candidate:
            self.candidate=chosen;self.candidate_since=now if chosen is not None else None
        for name in present:
            self.counts[name] = self.counts.get(name, 0)+1 if matches == [name] else 0
        if len(matches) == 1:
            name = matches[0]
            if self.counts[name] >= self.detection_frames and now >= self.next_allowed and self.delay_remaining<=0:
                self.retries=0;self.retry_visible=0
                self.pending = name
                self.since = self.last_tap = now
                self.absent = 0
                self.next_allowed = now + self.policies.get(name, {}).get('cooldown', 2)
                self.counts.clear()
                self.candidate=None;self.candidate_since=None
                return name
        return None
