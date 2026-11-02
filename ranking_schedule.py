"""Korean daily policy and durable purchase intents, independent of the PC clock zone."""
from datetime import datetime, timedelta, timezone
import json
import time
from pathlib import Path
from contextlib import contextmanager
from engine import SafetyStop

KST = timezone(timedelta(hours=9))


def korea_now():
    return datetime.now(KST)


def policy(now):
    if now.tzinfo is None:
        raise ValueError('시간대가 있는 현재 시간이 필요합니다.')
    now = now.astimezone(KST)
    weekend = now.weekday() >= 5
    hour = 20 if weekend else 13
    return now.date().isoformat(), now.hour >= hour, weekend


def defaults():
    return dict(enabled=False, ranking_steps=[], points_limit=100, star_limit=100,
                free_limit=100, exhausted='', points_price='', star_price='',ocr_prices=False)


def validate(config, records, ranking, recovery):
    if type(config.get('enabled', False)) is not bool:
        raise ValueError('랭킹 일정 사용 설정을 확인하세요.')
    if not config.get('enabled'):return
    if type(config.get('ocr_prices',False)) is not bool:raise ValueError('변경 금액 인식 설정을 확인하세요.')
    from ranking import validate_ranking
    from recovery import validate_recovery
    validate_ranking(dict(ranking, enabled=True, allow_star=False), records)
    validate_recovery(dict(recovery, enabled=True, target='league'), records)
    available = {r['id']: r for r in records if r['enabled'] and r['template']}
    steps = config.get('ranking_steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 30:
        raise ValueError('랭킹 자동 진입 이미지를 순서대로 등록하세요.')
    for key in steps:
        if key not in available or available[key].get('kind') == 'normal':
            raise ValueError('랭킹 진입 이미지는 사용 중인 감지/규칙 전용 이미지여야 합니다.')
    if len(set(steps)) != len(steps) or ranking['lobby'] in steps:
        raise ValueError('랭킹 진입 단계와 완료 이미지는 중복할 수 없습니다.')
    for key in ('points_limit', 'star_limit', 'free_limit'):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 100:
            raise ValueError('하루 재화별 갱신 한도는 1~100회입니다.')
    for key in ('exhausted', 'points_price', 'star_price'):
        if config.get(key) and config[key] not in available:
            raise ValueError('구매 금액/횟수 소진 확인 이미지를 확인하세요.')
        if config.get(key) and available[config[key]].get('kind') == 'normal':
            raise ValueError('금액/소진 확인은 일반 클릭이 아닌 감지 전용 이미지를 사용하세요.')
        if key.endswith('_price') and config.get(key) and config[key] in ranking.values():
            raise ValueError('결제 금액 확인은 기존 재화 아이콘과 다른 별도 이미지로 등록하세요.')


class ScheduleStore:
    def __init__(self, root):self.path = Path(root)/'ranking_schedule.json'
    def load(self):
        result = defaults()
        if self.path.exists():result.update(json.loads(self.path.read_text(encoding='utf-8')))
        return result
    def save(self, config):
        atomic_json(self.path, config)


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


@contextmanager
def schedule_lock(root):
    import os
    handle=open(Path(root)/'ranking_schedule.lock','a+b')
    locked=False
    try:
        if os.name=='nt':
            import msvcrt
            if handle.tell()==0:handle.write(b'0');handle.flush()
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:raise SafetyStop('동일 폴더의 랭킹 일정이 이미 실행 중입니다.') from error
        else:
            import fcntl
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:raise SafetyStop('동일 폴더의 랭킹 일정이 이미 실행 중입니다.') from error
        locked=True
        yield
    finally:
        if locked:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()


class DailyRun:
    def __init__(self, root, config, clock=korea_now, dry=False):
        self.path = Path(root)/('ranking_daily_dry.json' if dry else 'ranking_daily.json')
        self.config, self.clock = config, clock
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'days': {}}
        self.day = None

    def save(self):atomic_json(self.path, self.data)

    def due(self):
        day, due, _ = policy(self.clock())
        return due and self.data['days'].get(day, {}).get('status') not in ('done', 'blocked')

    def begin(self):
        day, due, _ = policy(self.clock())
        if not due:raise SafetyStop('랭킹 시작 시간 이전입니다.')
        self.day = day
        entry = self.data['days'].setdefault(day, dict(status='active', completed=0,
                           purchases={'free': 0, 'points': 0, 'star': 0}, pending=None))
        if entry['status'] != 'active':raise SafetyStop('오늘 랭킹은 이미 완료/확인 대기 상태입니다.')
        self.save()
        return entry

    @property
    def entry(self):return self.data['days'][self.day]

    def allowed(self, currency):
        day, due, weekend = policy(self.clock())
        return day == self.day and due and (currency != 'star' or weekend)

    def reserve(self, currency, amount=None, remaining=None,total=None):
        # Persist BEFORE sending a tap. An unknown outcome must never be paid again.
        if not self.allowed(currency):raise SafetyStop('현재 한국시간의 재화 사용 조건을 벗어났습니다.')
        if self.entry['pending']:
            raise SafetyStop('이전 갱신 결과 미확인 · 중복 구매 방지를 위해 확인 필요')
        if self.entry['purchases'][currency] >= self.config[currency+'_limit']:
            raise SafetyStop('하루 '+currency+' 갱신 한도 도달')
        self.entry['purchases'][currency] += 1
        self.entry['pending'] = currency
        self.entry['pending_completed']=self.entry['completed']
        if amount is not None:self.entry['last_payment']=dict(currency=currency,amount=amount)
        if remaining is not None:
            self.entry['paid_remaining']=remaining-1
            self.entry['paid_total']=total
        self.save()

    def verified(self):
        if self.entry['pending']:
            self.entry['pending'] = None
            self.entry.pop('pending_completed',None)
            self.save()

    def completed(self, count):
        if self.entry['completed'] != count:
            self.entry['completed'] = count
            self.save()

    def finish(self, reason, blocked=False):
        self.entry['status'] = 'blocked' if blocked else 'done'
        self.entry['reason'] = reason
        self.save()


class ScheduledRunner:
    """Supervise normal league sessions; hand off only at a verified league result."""
    def __init__(self, app, cfg, root):
        self.app, self.cfg, self.root = app, cfg, root
        self.daily = DailyRun(root, cfg['schedule'], dry=cfg['dry'])

    def run(self):
        with schedule_lock(self.root):self.run_locked()

    def run_locked(self):
        import copy
        from adb import ADB
        from ranking import validate_ranking
        validate(self.cfg['schedule'], self.cfg['buttons'], self.cfg['ranking'], self.cfg['recovery'])
        league = copy.deepcopy(self.cfg['recovery'])
        while not self.app.stop.is_set():
            cfg = copy.deepcopy(self.cfg)
            ranking_mode = self.daily.due()
            cfg['_scheduled'] = True
            cfg['recovery'] = copy.deepcopy(league)
            cfg['recovery']['enabled'] = True
            cfg['ranking']['enabled'] = ranking_mode
            cfg['_handoff'] = lambda: self.daily.due()
            if ranking_mode:
                self.daily.begin()
                _, _, weekend = policy(self.daily.clock())
                cfg['ranking']['allow_star'] = weekend
                # Missing weekend images blocks star spending, but free/P can still run.
                if weekend and not all(cfg['ranking'].get(k) for k in ('paid', 'star_confirm')):
                    cfg['ranking']['allow_star'] = False
                validate_ranking(cfg['ranking'], cfg['buttons'])
                cfg['ranking']['all_attempts'] = True
                cfg['_daily'] = self.daily
                cfg['recovery'].update(target='ranking', goal=cfg['ranking']['lobby'],
                                       steps=cfg['schedule']['ranking_steps'])
                self.app.emit('한국시간 랭킹 시작 · '+('주말 20시 · 무료/P/별' if weekend else '평일 13시 · 무료/P · 별 사용 금지'))
            else:
                cfg['recovery']['target'] = 'league'
                self.app.emit('리그 진행 · 다음 랭킹 시간까지 대기 (평일 13시 / 주말 20시, 한국시간)')
            if not cfg['dry']:
                adb = ADB(cfg['adb'], cfg['serial'])
                adb.connect()
                adb.stop_app(cfg['recovery']['package'])
                if self.app.stop.is_set():break
                adb.launch_app(cfg['recovery']['package'])
                cfg['_activate_route'] = True
            self.app.run_session(cfg)
            if cfg.get('_failed') or self.app.stop.is_set():break
            if ranking_mode:
                reason = cfg.get('_ranking_done', '랭킹 실행 확인 필요')
                self.daily.finish(reason, blocked=not bool(cfg.get('_ranking_done')))
                self.app.emit('오늘 랭킹 처리 완료 · '+reason+' · 리그로 복귀')
            elif not cfg.get('_yielded'):
                break


def guard_action(daily, machine, action, results, config):
    """Enforce time/currency at the final payment boundary, including recovery."""
    found = lambda key: bool(key and results.get(key, (False,))[0])
    r = machine.config
    before=daily.entry['completed']
    completed_after_purchase=machine.completed>daily.entry.get('pending_completed',before)
    daily.completed(machine.completed)
    # A stable active list or a completed later batch verifies the reserved
    # intent. Absence of a dialog alone is never payment success.
    if (daily.entry['pending'] and not found(r['refresh_dialog']) and
            (machine.refresh_verified or completed_after_purchase
             or (machine.stable(r['lobby'], {}) and machine.stable(r['series'], {}))
             or (action == r['series'] and found(r['lobby'])))):
        daily.verified()
        machine.refresh_verified=False
    if machine.phase == 'lobby' and machine.stable(r['lobby'], {}) and machine.stable(r['disabled'], {}):
        if daily.entry.get('paid_remaining')==0 and not daily.entry['pending']:
            machine.finished=True;machine.events.append('게임의 추가 갱신 횟수 소진 확인');return None
        if (not r.get('paid') and not any(found(r[k]) for k in ('series','points','refresh'))):
            raise SafetyStop('다음 랭킹 갱신 재화 미확인 · 별 갱신 이미지를 먼저 등록하세요.')
        exhausted = config.get('exhausted')
        weekday_star_only = (not policy(daily.clock())[2] and machine.stable(r['paid'], {})
                             and not any(found(r[k]) for k in ('series','points','refresh')))
        daily.exhausted_frames = getattr(daily,'exhausted_frames',0)+1 if exhausted and found(exhausted) else 0
        if weekday_star_only or daily.exhausted_frames>=3:
            machine.finished = True
            machine.events.append('오늘 사용 가능한 랭킹 도전 횟수 소진 확인')
            return None
    currency = next((kind for key, kind in [('refresh','free'),('points_confirm','points'),
                                            ('star_confirm','star')] if action and action == r[key]), None)
    if currency:
        amount=remaining=total=None
        if currency != 'free':
            if config.get('ocr_prices',False):
                proof=getattr(daily,'ocr_payment',None)
                daily.ocr_payment=None
                if (not proof or len(proof)!=5 or proof[0]!=action or type(proof[1]) is not int or not 1<=proof[1]<=999999999
                        or not 0<=time.monotonic()-proof[2]<3):
                    raise SafetyStop('랭킹 구매 금액 판독 미확인 · 구매하지 않습니다.')
                amount=proof[1]
                remaining,total=proof[3:]
                if type(remaining) is not int or type(total) is not int or not 1<=remaining<=total<=999:
                    raise SafetyStop('랭킹 구매 가능 횟수 판독 미확인 · 구매하지 않습니다.')
            else:
                price = config.get(currency+'_price')
                if not price or not found(price) or getattr(daily,'price_frames',{}).get(price,0)<3:
                    raise SafetyStop('랭킹 '+currency+' 구매 금액 미확인 · 금액까지 포함한 확인 이미지를 등록하세요.')
        daily.reserve(currency,amount,remaining,total)
    if action and not daily.allowed('free'):
        raise SafetyStop('랭킹 날짜/시간 변경 · 새 결제나 경기 시작 보류')
    return action
