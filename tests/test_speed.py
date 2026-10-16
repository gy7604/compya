import unittest
from actions import ActionMachine
from speed import MODES, DEFAULT_MODE, speed_settings
from rules import WorkflowMachine
from test_rules import fixtures


class SpeedTests(unittest.TestCase):
    def test_fast_has_no_extra_poll_delay_and_fires_first_observation(self):
        frames, interval = speed_settings('빠름 · 1회 인식')
        self.assertEqual((frames,interval),(1,0.0))
        m = ActionMachine(0, priority_order=['x'], detection_frames=frames)
        self.assertEqual(m.observe({'x':True},0),'x')

    def test_fast_still_requires_three_absent_frames_before_reclick(self):
        m=ActionMachine(0, priority_order=['x'], detection_frames=1)
        self.assertEqual(m.observe({'x':True},0),'x')
        self.assertIsNone(m.observe({'x':True},1))
        self.assertIsNone(m.observe({'x':False},1.1))
        self.assertIsNone(m.observe({'x':False},1.2))
        self.assertIsNone(m.observe({'x':True},2))
        for t in [2.1,2.2,2.3]:self.assertIsNone(m.observe({'x':False},t))
        self.assertEqual(m.observe({'x':True},2.4),'x')

    def test_fast_still_enforces_minimum_interval(self):
        m=ActionMachine(0,priority_order=['x'],detection_frames=1)
        m.observe({'x':True},0)
        for t in [.1,.2,.3]:m.observe({'x':False},t)
        self.assertIsNone(m.observe({'x':True},.4))
        self.assertEqual(m.observe({'x':True},2),'x')

    def test_default_keeps_previous_three_frame_behavior(self):
        self.assertEqual(speed_settings(DEFAULT_MODE),(3,.25))
        with self.assertRaises(ValueError):speed_settings('bad')

    def test_fast_rule_detects_condition_and_step_on_first_frames(self):
        records,rule=fixtures()
        m=WorkflowMachine(records,[rule],0,detection_frames=1)
        self.assertIsNone(m.observe({'goal':True},0))
        self.assertIsNotNone(m.active)
        self.assertEqual(m.observe({'goal':True,'pause':True},.1),'pause')
        for t in [.2,.3]:m.observe({'simulation':True},t)
        self.assertEqual(m.index,0)
        m.observe({'simulation':True},.4)
        self.assertEqual(m.index,1)
