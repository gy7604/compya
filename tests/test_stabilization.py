import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from diagnostics import write_stop_snapshot
from engine import SafetyStop
from stall import TransitionWatch
from test_recovery import fixture
from recovery import RecoveryMachine
from vision import Matcher


class StabilizationTests(unittest.TestCase):
    def test_animation_and_repeated_observations_do_not_extend_transition(self):
        watch=TransitionWatch()
        for now in (0,5,90,179):watch.observe(('click','button'),now)
        with self.assertRaises(SafetyStop):watch.observe(('click','button'),180)

    def test_normal_game_wait_is_unlimited_and_confirmed_transition_resets(self):
        watch=TransitionWatch()
        for now in (0,180,86400):watch.observe(None,now)
        watch.observe(('rule',0,'wait'),86401)
        watch.observe(('rule',0,'verify'),86580)
        watch.observe(('rule',0,'verify'),86759)
        watch.observe(None,86760)
        watch.observe(('rule',0,'verify'),90000)
        with self.assertRaises(SafetyStop):watch.observe(('rule',0,'verify'),90180)

    def test_explicit_delay_budget_is_respected(self):
        watch=TransitionWatch()
        watch.observe('pending',0,400)
        watch.observe('pending',399,400)
        with self.assertRaises(SafetyStop):watch.observe('pending',400,400)

    def test_recovery_deadline_covers_missing_goal_and_resolution_wait(self):
        records,cfg=fixture()
        machine=RecoveryMachine(cfg,records)
        machine.poll(False,0)
        machine.poll(False,2)
        machine.poll(True,3)
        # poll still runs while the capture loop waits for orientation.
        machine.poll(True,601)
        with self.assertRaises(SafetyStop):machine.poll(True,602)
        self.assertEqual(machine.launches,1)

    def test_popup_cannot_keep_recovery_alive_forever(self):
        records,cfg=fixture()
        machine=RecoveryMachine(cfg,records)
        machine.observe({'ad':True},0)
        for now in (1,2,300,599):machine.observe({'ad':True},now)
        with self.assertRaises(SafetyStop):machine.observe({'ad':True},600)

    def test_nonfinite_correlation_and_flat_candidate_are_never_clickable(self):
        path=Path(__file__).resolve().parents[1]/'assets'/'catcher.png'
        matcher=Matcher(path)
        frame=cv2.imencode('.png',np.full((400,600),120,np.uint8))[1].tobytes()
        for score in (np.nan,np.inf,1.):
            with patch('vision.cv2.matchTemplate',return_value=np.array([[score]],np.float32)):
                found,_,reported,_=matcher.find(frame)
                self.assertFalse(found)
                self.assertTrue(np.isfinite(reported))

    def test_diagnostic_context_and_previous_frame_are_bounded(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            context=dict(error='offline',last_click=dict(point=[10,20]))
            path=write_stop_snapshot(folder,b'current',{}, {},None,False,
                                     context=context,previous_png=b'previous')
            self.assertEqual((path.parent/'last_stop_previous.png').read_bytes(),b'previous')
            self.assertEqual(json.loads(path.with_suffix('.json').read_text(encoding='utf8'))['context'],context)
            write_stop_snapshot(folder,b'next',{}, {},None,False)
            self.assertFalse((path.parent/'last_stop_previous.png').exists())
