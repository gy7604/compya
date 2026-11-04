import shutil
import json
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

    def test_saved_opening_resumes_at_close_without_claiming_again(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root);g.path.write_text(json.dumps({'phase':'opening'}),encoding='utf-8')
            g=LeagueBonus(root);stop=threading.Event();popup=frame(g,['title','close'])
            actions=[g.observe(popup,t,stop) for t in range(8)]
            self.assertEqual([value for value in actions if value],[(360,1222)])
            self.assertEqual(g.phase,'closing');self.assertEqual(g.close_attempts,1)
            self.assertTrue(g.diagnostics()['matches']['close']['matched'])

    def test_close_only_retries_are_spaced_bounded_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root);stop=threading.Event();popup=frame(g,['title','close'])
            actions=[(t,g.observe(popup,t,stop)) for t in range(30)]
            self.assertEqual([(t,p) for t,p in actions if p],[(5,(360,1222)),(13,(360,1222)),(21,(360,1222))])
            g=LeagueBonus(root)
            for t in range(30,60):self.assertIsNone(g.observe(popup,t,stop))
            with self.assertRaises(SafetyStop):g.observe(popup,76,stop)
            self.assertEqual(g.close_attempts,3)

    def test_close_silhouette_ignores_colored_background_but_still_requires_bonus_title(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root,dry=True);stop=threading.Event();png=frame(g,['title','close'])
            image=cv2.imdecode(np.frombuffer(png,np.uint8),1);m=g.matchers['close'];x,y,_,_=m.roi;h,w=m.template.shape
            patch=image[y:y+h,x:x+w];patch[m.template<140]=(170,35,90)
            png=cv2.imencode('.png',image)[1].tobytes()
            self.assertTrue(m.find(png)[0]);self.assertEqual([g.observe(png,t,stop) for t in (0,1,5)], [None,None,(360,1222)])
            other=self.setup_guard(root,dry=True)
            for t in (0,1,5):self.assertIsNone(other.observe(frame(other,['close']),t,stop))

    def test_missing_close_control_never_uses_a_guessed_tap_and_diagnostics_explain_failure(self):
        with tempfile.TemporaryDirectory() as root:
            g=self.setup_guard(root);g.reserve('opening',0);stop=threading.Event();title=frame(g,['title'])
            for t in range(45):self.assertIsNone(g.observe(title,t,stop))
            with self.assertRaisesRegex(SafetyStop,'닫기 X=False'):g.observe(title,46,stop)
            self.assertTrue(g.diagnostics()['matches']['title']['matched'])
            self.assertFalse(g.diagnostics()['matches']['close']['matched'])
