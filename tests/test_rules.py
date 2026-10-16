import copy
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import Mock, patch
from app import App
from actions import ActionMachine
from engine import SafetyStop
from registry import new_button
from rules import WorkflowMachine, RuleStore, new_rule, validate_rules


def fixtures():
    records={}
    for key,kind in [('normal','normal'),('other','normal'),('goal','condition'),('pause','step'),
                     ('simulation','step'),('confirm','step'),('done','condition')]:
        r=new_button(key)
        r.update(id=key,kind=kind,enabled=True,template=key+'.png',cooldown=.5)
        records[key]=r
    rule=new_rule('목표 달성 → 시뮬레이션')
    rule.update(enabled=True,trigger='goal',success='done',steps=[{'button':key,'verify':'next'} for key in ['pause','simulation','confirm']])
    return records,rule


class RuleTests(unittest.TestCase):
    def test_normal_no_image_can_wait_for_a_day(self):
        records,_=fixtures()
        m=WorkflowMachine(records,[],0,timeout=1)
        for t in [1,180,3600,86400]:self.assertIsNone(m.observe({},t))
        for t in [86401,86402]:self.assertIsNone(m.observe({'normal':True},t))
        self.assertEqual(m.observe({'normal':True},86403),'normal')

    def test_normal_visible_button_and_queued_rule_can_wait_indefinitely(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0,timeout=1)
        for t in [0,.25,.5]:m.observe({'normal':True},t)
        for t in [1,1.25,1.5]:m.observe({'normal':True,'goal':True},t)
        self.assertIsNotNone(m.queued)
        self.assertIsNone(m.observe({'normal':True},86400))
        for t in [86401,86402,86403,86404]:m.observe({},t)
        self.assertIsNotNone(m.active)

    def test_rule_step_disappearance_can_wait_for_a_day(self):
        records,rule=fixtures()
        rule.update(steps=[{'button':'pause','verify':'disappear'}],success='')
        m=WorkflowMachine(records,[rule],0)
        for t in range(6):m.observe({'goal':True,'pause':True},t)
        self.assertIsNone(m.observe({'pause':True},86400))
        self.assertEqual(m.phase,'verify')
        for t in [86401,86402,86403]:m.observe({},t)
        self.assertTrue(m.finished)

    def runner(self, dry, progress=False):
        records,rule=fixtures()
        app=App.__new__(App)
        app.stop=threading.Event()
        app.stop.wait=Mock(return_value=False)
        app.emit=Mock()
        app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
        adb=Mock();adb.screenshot.side_effect=list(range(24))
        keys=list(records)
        def factory(path,*args):
            key=path.removesuffix('.png')
            def find(frame):
                stage='pause' if 3<=frame<6 else 'simulation' if 6<=frame<12 else 'confirm' if 12<=frame<18 else 'done' if frame>=18 else ''
                present=key in ('normal','other','goal',stage)
                return present,(keys.index(key)*10,100),.99 if present else .1,(720,1280)
            matcher=Mock();matcher.find.side_effect=find;return matcher
        cfg=dict(buttons=list(records.values()),rules=[rule],dry=dry,adb='adb',serial='device',wait=180)
        if progress:
            cfg['progress']=dict(enabled=True,watch=['normal','other'],point=[450,500],size=[720,1280],delay=.5,interval=.5)
        with patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=factory),patch('app.time.monotonic',side_effect=range(100)):
            app.run(cfg)
        self.assertTrue(any('규칙 완료 설정' in c.args[0] for c in app.emit.call_args_list))
        return adb

    def test_actual_runner_only_clicks_ordered_rule_steps_and_stops(self):
        adb=self.runner(False)
        self.assertEqual([c.args[0] for c in adb.tap.call_args_list],[(30,100),(40,100),(50,100)])

    def test_rule_runner_dry_run_never_sends_taps(self):
        adb=self.runner(True)
        adb.tap.assert_not_called()

    def test_progress_clicks_do_not_interrupt_rule_execution(self):
        adb=self.runner(False,progress=True)
        self.assertEqual([c.args[0] for c in adb.tap.call_args_list],[(30,100),(40,100),(50,100)])

    def test_priority_resolves_two_visible_buttons(self):
        m=ActionMachine(0,priority_order=['PLAY BALL','coach'])
        self.assertIsNone(m.observe({'PLAY BALL':True,'coach':True},0))
        self.assertIsNone(m.observe({'PLAY BALL':True,'coach':True},.25))
        self.assertEqual(m.observe({'PLAY BALL':True,'coach':True},.5),'PLAY BALL')
        self.assertIsNone(m.observe({'PLAY BALL':True,'coach':True},1))

    def test_workflow_normal_priority_uses_record_order(self):
        records,_=fixtures()
        m=WorkflowMachine(records,[],0)
        for t in [0,.25]:self.assertIsNone(m.observe({'normal':True,'other':True},t))
        self.assertEqual(m.observe({'normal':True,'other':True},.5),'normal')

    def test_condition_images_and_rule_steps_are_not_normal_taps(self):
        records,_=fixtures()
        m=WorkflowMachine(records,[],0)
        for t in range(10):
            self.assertIsNone(m.observe({'goal':True,'pause':True},t))

    def test_full_goal_to_simulation_sequence(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0)
        taps=[]
        for t in range(24):
            key='pause' if 3<=t<6 else 'simulation' if 6<=t<12 else 'confirm' if 12<=t<18 else 'done' if t>=18 else ''
            tap=m.observe({'goal':True,key:True},t)
            if tap:taps.append(tap)
        self.assertEqual(taps,['pause','simulation','confirm'])
        self.assertTrue(m.finished)
        self.assertTrue(any('최종 이미지 확인' in e for e in m.events))

    def test_goal_preempts_normal_click_when_both_stabilize(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0)
        for t in [0,.25,.5]:
            self.assertIsNone(m.observe({'goal':True,'normal':True},t))
        self.assertIsNotNone(m.active)

    def test_rule_cannot_skip_missing_first_step(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0)
        for t in range(6):
            self.assertIsNone(m.observe({'goal':True,'confirm':True},t))
        self.assertIsNone(m.observe({'confirm':True},86400))
        self.assertEqual(m.index,0)
        self.assertEqual(m.phase,'wait')

    def test_next_image_visible_before_tap_does_not_confirm_transition(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0)
        for t in range(9):m.observe({'goal':True,'pause':True,'simulation':True},t)
        self.assertEqual(m.index,0)
        self.assertEqual(m.phase,'verify')
        m.observe({'goal':True,'pause':True},9)
        for t in [10,11,12]:m.observe({'goal':True,'pause':True,'simulation':True},t)
        self.assertEqual(m.index,1)

    def test_resume_once_prevents_retrigger_even_after_goal_reappears(self):
        records,rule=fixtures()
        rule.update(steps=[{'button':'pause','verify':'disappear'}],success='',finish='resume')
        m=WorkflowMachine(records,[rule],0)
        for t in range(6):m.observe({'goal':True,'pause':True},t)
        for t in [6,7,8]:m.observe({'goal':True},t)
        self.assertIsNone(m.active)
        for t in [9,10,11]:m.observe({},t)
        for t in [12,13,14]:m.observe({'goal':True},t)
        self.assertIsNone(m.active)

    def test_repeating_rule_requires_goal_to_disappear_after_completion(self):
        records,rule=fixtures()
        rule.update(steps=[{'button':'pause','verify':'disappear'}],success='',finish='resume',once=False)
        m=WorkflowMachine(records,[rule],0)
        for t in range(6):m.observe({'goal':True,'pause':True},t)
        for t in [6,7,8,9,10,11]:m.observe({'goal':True},t)
        self.assertIsNone(m.active)
        for t in [12,13,14]:m.observe({},t)
        for t in [15,16,17]:m.observe({'goal':True},t)
        self.assertIsNotNone(m.active)

    def test_normal_pending_latch_must_clear_before_rule_starts(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0)
        for t in [0,.25,.5]:m.observe({'normal':True},t)
        for t in [1,1.25,1.5]:m.observe({'normal':True,'goal':True},t)
        self.assertIsNone(m.active)
        for t in [2,2.25,2.5]:m.observe({'goal':True},t)
        m.observe({'goal':True},2.75)
        self.assertIsNotNone(m.active)

    def test_confirmed_goal_remains_queued_if_image_disappears(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0)
        for t in [0,.25,.5]:m.observe({'normal':True},t)
        for t in [1,1.25,1.5]:m.observe({'normal':True,'goal':True},t)
        self.assertIsNotNone(m.queued)
        for t in [2,2.25,2.5,2.75]:m.observe({},t)
        self.assertIsNotNone(m.active)

    def test_invalid_reference_and_last_next_without_success_are_rejected(self):
        records,rule=fixtures()
        for change in [dict(trigger='normal'),dict(success=''),dict(success='goal')]:
            r=copy.deepcopy(rule);r.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_rules([r],list(records.values()))

    def test_disabled_incomplete_rule_and_store_round_trip(self):
        records,_=fixtures()
        r=new_rule()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=RuleStore(folder)
            store.save([r],list(records.values()))
            self.assertEqual(store.load(records),[r])


    def test_next_image_and_final_image_can_wait_indefinitely_then_resume(self):
        records,rule=fixtures()
        rule.update(steps=[{'button':'pause','verify':'next'}])
        m=WorkflowMachine(records,[rule],0)
        for t in range(6):m.observe({'goal':True,'pause':True},t)
        self.assertIsNone(m.observe({},86400))
        self.assertEqual(m.phase,'verify')
        for t in [86401,86402,86403]:m.observe({'done':True},t)
        self.assertEqual(m.phase,'success')
        self.assertIsNone(m.observe({},172800))
        self.assertEqual(m.phase,'success')
        for t in [172801,172802,172803]:m.observe({'done':True},t)
        self.assertTrue(m.finished)
