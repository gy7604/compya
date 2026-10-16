import tempfile
import unittest
from pathlib import Path
from actions import ActionMachine
from registry import Registry,new_button,validate
from rules import WorkflowMachine,new_rule

class DelayTests(unittest.TestCase):
    def machine(self,frames=1):return ActionMachine(0,policies={'reward':{'click_delay':3,'cooldown':2}},priority_order=['other','reward'],detection_frames=frames)
    def test_waits_while_image_visible_then_taps_once(self):
        m=self.machine()
        for t in [0,1,2.99]:self.assertIsNone(m.observe({'reward':True},t))
        self.assertAlmostEqual(m.delay_remaining,.01)
        self.assertEqual(m.observe({'reward':True},3),'reward')
        for t in [4,10,86400]:self.assertIsNone(m.observe({'reward':True},t))
    def test_disappearance_cancels_delay_and_reappearance_restarts(self):
        m=self.machine();m.observe({'reward':True},0);m.observe({},2.9)
        self.assertIsNone(m.observe({'reward':True},5))
        self.assertIsNone(m.observe({'reward':True},7.9))
        self.assertEqual(m.observe({'reward':True},8),'reward')
    def test_higher_priority_button_cancels_candidate_without_blocking_it(self):
        m=self.machine();m.observe({'reward':True},0)
        self.assertEqual(m.observe({'other':True,'reward':True},1),'other')
    def test_detection_frames_and_cooldown_are_still_required(self):
        m=self.machine(3);m.next_allowed=10
        for t in [0,1,3,9]:self.assertIsNone(m.observe({'reward':True},t))
        self.assertEqual(m.observe({'reward':True},10),'reward')
    def test_old_button_without_field_still_clicks_immediately(self):
        m=ActionMachine(0,policies={'x':{}},detection_frames=1)
        self.assertEqual(m.observe({'x':True},0),'x')
    def test_rule_step_delay_resets_when_image_disappears(self):
        goal=new_button('goal');goal.update(id='goal',kind='condition',enabled=True,template='goal.png')
        reward=new_button('reward');reward.update(id='reward',kind='step',enabled=True,template='reward.png',click_delay=3)
        rule=new_rule();rule.update(enabled=True,trigger='goal',steps=[dict(button='reward',verify='disappear')],success='',finish='resume')
        m=WorkflowMachine({'goal':goal,'reward':reward},[rule],0,detection_frames=1)
        m.observe({'goal':True},0)
        self.assertIsNone(m.observe({'reward':True},1))
        self.assertIn('클릭 전 대기',m.status)
        m.observe({},3)
        self.assertIsNone(m.observe({'reward':True},4))
        self.assertIsNone(m.observe({'reward':True},6.9))
        self.assertEqual(m.observe({'reward':True},7),'reward')
        for t in [8,9,10]:m.observe({},t)
        self.assertIsNone(m.active)
    def test_setting_validation_and_round_trip(self):
        record=new_button('reward');record['click_delay']=3
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=Registry(folder);store.save([record]);self.assertEqual(store.load({})[0]['click_delay'],3)
        for value in [-1,float('nan'),float('inf'),3601]:
            with self.assertRaises(ValueError):validate([dict(record,click_delay=value)])
