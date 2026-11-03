import shutil
import tempfile
import threading
import unittest
from pathlib import Path
import cv2
import numpy as np
from league_bonus import LeagueBonus,FILES
from engine import SafetyStop

ROOT=Path(__file__).resolve().parents[1]


def frame(guard,keys):
    image=np.zeros((1280,720,3),np.uint8)
    for key in keys:
        m=guard.matchers[key];x,y,_,_=m.roi;h,w=m.template.shape
        image[y:y+h,x:x+w]=cv2.cvtColor(m.template,cv2.COLOR_GRAY2BGR)
    return cv2.imencode('.png',image)[1].tobytes()


class BonusTests(unittest.TestCase):
    def setup_guard(self,root,dry=False):
        assets=Path(root)/'assets';assets.mkdir(exist_ok=True)
        for name in FILES+('league_next_match.png',):shutil.copy2(ROOT/'assets'/name,assets/name)
        return LeagueBonus(root,dry=dry)

    def test_claim_once_close_after_animation_and_verify_completion(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root);stop=threading.Event()
            ready=frame(g,['lobby','ready']);popup=frame(g,['title','close']);done=frame(g,['lobby','done'])
            self.assertIsNone(g.observe(ready,0,stop));self.assertTrue(g.hold)
            self.assertEqual(g.observe(ready,1,stop),(350,1170))
            self.assertIsNone(g.observe(ready,2,stop))
            self.assertIsNone(g.observe(popup,3,stop));self.assertIsNone(g.observe(popup,7,stop))
            self.assertEqual(g.observe(popup,8,stop),(360,1222));self.assertIsNone(g.observe(popup,9,stop))
            g.observe(done,10,stop);g.observe(done,11,stop)
            self.assertFalse(g.hold);self.assertEqual(g.phase,'idle')
            self.assertIn('수령 완료',g.events[-1])

    def test_restart_never_repeats_ambiguous_claim(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root);stop=threading.Event();png=frame(g,['lobby','ready'])
            g.observe(png,0,stop);g.observe(png,1,stop)
            g=LeagueBonus(root)
            self.assertIsNone(g.observe(png,2,stop));self.assertIsNone(g.observe(png,3,stop))
            with self.assertRaises(SafetyStop):g.observe(png,48,stop)

    def test_ready_without_lobby_or_popup_without_close_does_not_click(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root,dry=True);stop=threading.Event()
            for keys in (['ready'],['title'],['close'],['lobby','done'],[]):
                png=frame(g,keys)
                for now in (0,1,10):self.assertIsNone(g.observe(png,now,stop))
            self.assertFalse(g.path.exists())
