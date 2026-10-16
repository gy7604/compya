import tempfile
import unittest
from pathlib import Path
from actions import ActionMachine
from registry import new_button,Registry,validate
from rules import WorkflowMachine

class RetryTests(unittest.TestCase):
    def machine(self,**changes):
        policy=dict(retry_interval=8,retry_limit=2,cooldown=2,disappear_frames=3);policy.update(changes)
        return ActionMachine(0,policies={'pitch':policy},detection_frames=1)
    def test_retry_interval_limit_and_never_unlimited(self):
        m=self.machine();self.assertEqual(m.observe({'pitch':True},0),'pitch')
        for t in [1,2,7.9]:self.assertIsNone(m.observe({'pitch':True},t))
        self.assertEqual(m.observe({'pitch':True},8),'pitch')
        for t in [9,10,15.9]:self.assertIsNone(m.observe({'pitch':True},t))
        self.assertEqual(m.observe({'pitch':True},16),'pitch')
        for t in [17,18,24,86400]:self.assertIsNone(m.observe({'pitch':True},t))
        self.assertEqual(m.retries,2)
    def test_other_visible_button_blocks_retry_and_absence_resets_stability(self):
        m=self.machine();m.observe({'pitch':True},0)
        for t in [1,2,8]:self.assertIsNone(m.observe({'pitch':True,'other':True},t))
        m.observe({},9)
        self.assertIsNone(m.observe({'pitch':True},10));self.assertIsNone(m.observe({'pitch':True},11))
        self.assertEqual(m.observe({'pitch':True},12),'pitch')
    def test_one_absent_frame_rearms_and_retry_budget_resets(self):
        m=self.machine(disappear_frames=1);m.observe({'pitch':True},0)
        self.assertIsNone(m.observe({},1));self.assertIsNone(m.pending)
        self.assertEqual(m.observe({'pitch':True},2),'pitch');self.assertEqual(m.retries,0)
    def test_default_never_retries(self):
        m=ActionMachine(0,detection_frames=1);m.observe({'pitch':True},0)
        for t in [1,2,8,16,86400]:self.assertIsNone(m.observe({'pitch':True},t))
    def test_cooldown_and_preclick_delay_bound_retries(self):
        m=self.machine(cooldown=20);m.observe({'pitch':True},0)
        for t in [1,2,8,19]:self.assertIsNone(m.observe({'pitch':True},t))
        self.assertEqual(m.observe({'pitch':True},20),'pitch')
    def test_workflow_emits_named_retry_event(self):
        r=new_button('포수리드');r.update(id='pitch',enabled=True,template='a.png',retry_limit=2,retry_interval=8)
        m=WorkflowMachine({'pitch':r},[],0,detection_frames=1)
        m.observe({'pitch':True},0)
        for t in [1,2,8]:m.observe({'pitch':True},t)
        self.assertIn('자동 재클릭 · 포수리드 · 1/2회',m.events)
    def test_validation_and_storage(self):
        r=new_button();r.update(retry_limit=2,retry_interval=8,disappear_frames=1)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=Registry(folder);store.save([r]);self.assertEqual(store.load({}),[r])
        for changes in [dict(retry_limit=-1),dict(retry_limit=1.5),dict(retry_interval=float('nan')),dict(retry_interval=1),dict(disappear_frames=0),dict(disappear_frames=4)]:
            with self.assertRaises(ValueError):validate([dict(r,**changes)])
