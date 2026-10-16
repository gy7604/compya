import unittest
from engine import PitchMachine, Settings, State, SafetyStop


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.m = PitchMachine(Settings(), 0)

    def frames(self, present, times):
        return [self.m.observe(present, t) for t in times]

    def test_cycle_and_duplicate_prevention(self):
        self.assertEqual(self.frames(True, [0, .25, .5]), [False, False, True])
        self.assertFalse(any(self.frames(True, [1, 2, 3])))
        self.assertFalse(any(self.frames(False, [3.25, 3.5, 3.75])))
        self.assertEqual(self.m.state, State.RETURN)
        self.assertEqual(self.frames(True, [4, 4.25, 4.5]), [False, False, True])
        self.assertEqual(self.m.taps, 2)

    def test_flicker_does_not_rearm(self):
        self.frames(True, [0, .25, .5])
        self.frames(False, [1, 1.25])
        self.frames(True, [1.5, 1.75, 2])
        self.assertEqual(self.m.state, State.DISAPPEAR)
        self.assertEqual(self.m.taps, 1)

    def test_disappearance_timeout(self):
        self.frames(True, [0, .25, .5])
        with self.assertRaises(SafetyStop):
            self.m.observe(True, 8.5)

    def test_initial_recognition_timeout(self):
        with self.assertRaises(SafetyStop):
            self.m.observe(False, 180)

    def test_return_timeout(self):
        self.frames(True, [0, .25, .5])
        self.frames(False, [1, 1.25, 1.5])
        with self.assertRaises(SafetyStop):
            self.m.observe(False, 181.5)

    def test_cooldown(self):
        self.frames(True, [0, .1, .2])
        self.frames(False, [.3, .4, .5])
        self.assertFalse(any(self.frames(True, [.6, .7, .8, 1])))
        self.assertTrue(self.m.observe(True, 2.3))


if __name__ == '__main__':
    unittest.main()
