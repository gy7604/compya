"""Declarative image-triggered workflows. No arbitrary scripts or coordinates."""
import json
import math
from pathlib import Path
import uuid
from actions import ActionMachine
from engine import SafetyStop


def new_rule(name='새 조건 규칙'):
    return dict(id=uuid.uuid4().hex, name=name, enabled=False, trigger='', steps=[],
                success='', timeout=30., once=True, finish='stop')


def validate_rules(rules, records):
    by_id = {r['id']: r for r in records}
    ids, names = set(), set()
    for rule in rules:
        if rule['id'] in ids or not rule['name'].strip() or rule['name'].strip() in names:
            raise ValueError('규칙 ID와 이름은 중복되거나 비어 있을 수 없습니다.')
        ids.add(rule['id']); names.add(rule['name'].strip())
        if type(rule['enabled']) is not bool or type(rule['once']) is not bool:
            raise ValueError('규칙 사용 여부가 올바르지 않습니다.')
        if rule['finish'] not in ('stop', 'resume'):
            raise ValueError('규칙 완료 동작이 올바르지 않습니다.')
        if not math.isfinite(float(rule['timeout'])) or not .5 <= float(rule['timeout']) <= 3600:
            raise ValueError('단계 대기 제한은 0.5~3600초입니다.')
        if len(rule['steps']) > 30:
            raise ValueError('규칙 단계는 최대 30개입니다.')
        for index, step in enumerate(rule['steps']):
            if step['verify'] not in ('disappear', 'next'):
                raise ValueError('단계 확인 방식이 올바르지 않습니다.')
            if rule['enabled'] and step['verify'] == 'next' and index == len(rule['steps'])-1 and not rule['success']:
                raise ValueError('마지막 단계에서 다음 이미지 확인을 쓰려면 최종 확인 이미지를 지정하세요.')
            if step['verify'] == 'next' and index+1 < len(rule['steps']) and step['button'] == rule['steps'][index+1]['button']:
                raise ValueError('같은 버튼을 연속으로 누를 때는 사라짐 확인을 사용하세요.')
        if not rule['enabled']:
            continue
        if not rule['steps']:
            raise ValueError('사용할 규칙에 클릭 단계를 하나 이상 추가하세요.')
        if rule['success'] and rule['success'] == rule['trigger']:
            raise ValueError('최종 확인 이미지는 시작 조건과 달라야 합니다.')
        refs = [(rule['trigger'], 'condition')] + [(s['button'], 'step') for s in rule['steps']]
        if rule['success']:
            refs.append((rule['success'], 'condition'))
        for key, kind in refs:
            record = by_id.get(key)
            if not record or not record['enabled'] or not record['template'] or record.get('kind', 'normal') != kind:
                label = record['name'] if record else key or '미지정'
                raise ValueError(f'{rule["name"]} / {label}: 조건 이미지는 감지 전용, 클릭 단계는 규칙 전용으로 등록하고 사용을 켜세요.')


class RuleStore:
    def __init__(self, root):
        self.path = Path(root)/'rules.json'

    def load(self, records):
        rules = json.loads(self.path.read_text(encoding='utf-8'))['rules'] if self.path.exists() else []
        # Broken references are retained for editing; execution is validated at start.
        return rules

    def save(self, rules, records):
        validate_rules(rules, records)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'version': 1, 'rules': rules}, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)


class WorkflowMachine:
    def __init__(self, records, rules, now, timeout=None, detection_frames=3):
        validate_rules(rules, list(records.values()))
        self.records = records
        self.rules = [r for r in rules if r['enabled']]
        self.detection_frames = detection_frames
        self.normal_ids = [key for key, r in records.items() if r.get('kind', 'normal') == 'normal']
        self.normal = self.new_normal(now)
        self.active = None
        self.queued = None
        self.index = 0
        self.phase = 'idle'
        self.since = now
        self.visible = self.absent = 0
        self.condition_counts = {}
        self.rearmed = {r['id']: True for r in self.rules}
        self.completed = set()
        self.trigger_absent = {}
        self.next_allowed = float('-inf')
        self.finished = False
        self.events = []
        self.next_armed = False
        self.success_armed = False

    def new_normal(self, now):
        return ActionMachine(now, policies=self.records, priority_order=self.normal_ids, detection_frames=self.detection_frames)

    @property
    def pending(self):
        if self.active:
            return self.active['steps'][self.index]['button'] if self.phase == 'verify' else None
        return self.normal.pending

    @property
    def status(self):
        if self.active:
            label = {'wait':'버튼 나타남 대기', 'verify':'클릭 후 확인', 'success':'최종 이미지 대기'}[self.phase]
            return f'규칙 {self.active["name"]} · {self.index+1}/{len(self.active["steps"])} · {label}'
        return f'{self.records[self.normal.pending]["name"]} 사라짐 대기' if self.normal.pending else '버튼 대기 · 우선순위 적용'

    def update_conditions(self, present):
        for rule in self.rules:
            key = rule['id']
            visible = present.get(rule['trigger'], False)
            self.condition_counts[key] = self.condition_counts.get(key, 0)+1 if visible else 0
            if self.active is None and not self.rearmed[key] and key not in self.completed:
                self.trigger_absent[key] = 0 if visible else self.trigger_absent.get(key, 0)+1
                if self.trigger_absent[key] >= 3:
                    self.rearmed[key] = True

    def observe(self, present, now):
        if self.finished:
            return None
        self.update_conditions(present)
        if self.active:
            return self.observe_rule(present, now)
        if self.queued is None:
            self.queued = next((r for r in self.rules if self.rearmed[r['id']] and r['id'] not in self.completed
                                and self.condition_counts.get(r['id'], 0) >= self.detection_frames), None)
            if self.queued and self.normal.pending is not None:
                self.events.append(f'조건 감지 · {self.queued["name"]} 예약, 현재 클릭의 사라짐 확인 대기')
        ready = self.queued
        if ready and self.normal.pending is None and now >= self.normal.next_allowed:
            self.active = ready
            self.queued = None
            self.index, self.phase, self.since = 0, 'wait', now
            self.visible = self.absent = 0
            self.success_armed = not present.get(ready['success'], False)
            self.rearmed[ready['id']] = False
            self.events.append(f'조건 감지 · {ready["name"]} 시작 (조건 이미지는 클릭하지 않음)')
            return None
        # A stable condition takes precedence over issuing further normal taps.
        if ready and self.normal.pending is None:
            return None
        return self.normal.observe({key: present.get(key, False) for key in self.normal_ids}, now)

    def observe_rule(self, present, now):
        rule = self.active
        step = rule['steps'][self.index]
        key = step['button']
        record = self.records[key]
        if rule['success'] and not present.get(rule['success'], False):
            self.success_armed = True
        limit = record['disappear_timeout'] if self.phase == 'verify' and step['verify'] == 'disappear' else rule['timeout']
        if now-self.since >= limit:
            phase = {'wait':'버튼 나타남 대기', 'verify':'클릭 후 확인', 'success':'최종 이미지 대기'}[self.phase]
            raise SafetyStop(f'규칙 "{rule["name"]}" {self.index+1}단계 "{record["name"]}" · {phase} {limit:g}초 시간 초과')
        if self.phase == 'wait':
            self.visible = self.visible+1 if present.get(key, False) else 0
            if self.visible >= self.detection_frames and now >= self.next_allowed:
                self.phase, self.since = 'verify', now
                self.visible = self.absent = 0
                self.next_allowed = now+record['cooldown']
                target = rule['steps'][self.index+1]['button'] if self.index+1 < len(rule['steps']) else rule['success']
                self.next_armed = not present.get(target, False)
                return key
        elif self.phase == 'verify':
            if step['verify'] == 'disappear':
                self.absent = 0 if present.get(key, False) else self.absent+1
                confirmed = self.absent >= 3
            else:
                target = rule['steps'][self.index+1]['button'] if self.index+1 < len(rule['steps']) else rule['success']
                if not present.get(target, False):
                    self.next_armed = True
                self.visible = self.visible+1 if self.next_armed and present.get(target, False) else 0
                confirmed = self.visible >= 3
            if confirmed:
                self.visible = self.absent = 0
                self.since = now
                if self.index+1 < len(rule['steps']):
                    self.index += 1
                    self.phase = 'wait'
                elif rule['success']:
                    self.phase = 'success'
                else:
                    self.complete(now, verified=False)
        else:  # final success image must itself remain visible for 3 frames
            self.visible = self.visible+1 if self.success_armed and present.get(rule['success'], False) else 0
            if self.visible >= 3:
                self.complete(now, verified=True)
        return None

    def complete(self, now, verified):
        rule = self.active
        self.events.append(f'규칙 완료 · {rule["name"]} · {"최종 이미지 확인" if verified else "클릭 단계 완료, 최종 화면 미확인"}')
        if rule['once']:
            self.completed.add(rule['id'])
        self.trigger_absent[rule['id']] = 0
        self.finished = rule['finish'] == 'stop'
        self.active, self.phase = None, 'idle'
        self.normal = self.new_normal(now)
        self.normal.next_allowed = self.next_allowed
