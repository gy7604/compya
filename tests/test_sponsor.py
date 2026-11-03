import tempfile
import threading
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock
from sponsor import Sponsor,slot_key,KST
from engine import SafetyStop

ROOT=Path(__file__).resolve().parents[1]


class SponsorTests(unittest.TestCase):
    def fixture(self,root,clock=None,dry=False):
        s=Sponsor(ROOT,dry=True,clock=clock);s.path=Path(root)/'sponsor_state.json';s.dry=dry
        if not dry and s.path.exists():
            import json
            s.data=json.loads(s.path.read_text())
        s.scan=Mock();return s

    def observe(self,s,view,now):
        s.scan.return_value=view;return s.observe(b'frame',now,threading.Event())

    def test_kst_four_boundaries_and_utc_conversion(self):
        for h in (0,6,12,18):
            before=datetime(2026,11,4,h,tzinfo=KST)-timedelta(seconds=1)
            at=before+timedelta(seconds=1)
            self.assertNotEqual(slot_key(before),slot_key(at))
            self.assertEqual(slot_key(at),slot_key(at.astimezone(timezone.utc)))
        with self.assertRaises(ValueError):slot_key(datetime(2026,11,4,6))

    def test_routes_at_lobby_and_claims_exactly_once_then_returns(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.fixture(root,lambda:datetime(2026,11,4,6,tzinfo=KST))
            self.assertIsNone(self.observe(s,None,0));self.assertFalse(s.hold)
            self.observe(s,'league',1);self.assertEqual(self.observe(s,'league',2),(360,1247))
            self.observe(s,'home',8);self.assertEqual(self.observe(s,'home',9),(245,1120))
            self.observe(s,'sponsor',15);self.assertEqual(self.observe(s,'sponsor',16),(360,1145))
            for t in (22,30,40):self.assertIsNone(self.observe(s,'sponsor',t))
            self.observe(s,'reward',41);self.assertEqual(self.observe(s,'reward',42),(360,803))
            self.observe(s,'sponsor',48);self.assertEqual(self.observe(s,'sponsor',49),(360,1247))
            self.observe(s,'home',55);self.assertIsNone(self.observe(s,'home',56));self.assertTrue(s.finished)
            self.assertFalse(s.active);self.assertIsNone(s.due());self.assertFalse(s.hold)

    def test_restart_pending_claim_never_presses_settle_again(self):
        with tempfile.TemporaryDirectory() as root:
            clock=lambda:datetime(2026,11,4,13,tzinfo=KST)
            s=self.fixture(root,clock);self.observe(s,'sponsor',0);self.observe(s,'sponsor',1)
            s=self.fixture(root,clock)
            for t in (2,3,10):self.assertIsNone(self.observe(s,'sponsor',t))
            self.observe(s,'reward',11);self.assertEqual(self.observe(s,'reward',12),(360,803))
            s=self.fixture(root,clock);self.observe(s,'sponsor',13)
            self.assertEqual(self.observe(s,'sponsor',14),(360,1247))

    def test_late_start_services_only_latest_slot_and_completed_slot_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as root:
            now=[datetime(2026,11,4,17,59,tzinfo=KST)];s=self.fixture(root,lambda:now[0])
            key=s.due();self.assertEqual(datetime.fromisoformat(key).hour,12)
            s.data['slots'][key]={'state':'done'};s.save()
            self.assertIsNone(s.due());now[0]+=timedelta(minutes=1)
            self.assertEqual(datetime.fromisoformat(s.due()).hour,18)
            self.assertEqual(len(s.data['slots']),1)

    def test_gameplay_and_unknown_screens_never_trigger_navigation(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.fixture(root)
            for t in range(5):self.assertIsNone(self.observe(s,None,t));self.assertFalse(s.hold)
            self.assertFalse(s.active)

    def test_unknown_reward_not_claimed_and_navigation_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.fixture(root);self.observe(s,'home',0);self.observe(s,'home',1)
            self.observe(s,'reward',7)
            with self.assertRaises(SafetyStop):self.observe(s,'reward',8)
            with self.assertRaises(SafetyStop):self.observe(s,None,123)

    def test_dry_run_never_creates_a_ledger_and_stop_cancels_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            s=self.fixture(root,dry=True);self.observe(s,'sponsor',0);self.observe(s,'sponsor',1)
            self.assertFalse(s.path.exists());stop=threading.Event();stop.set()
            self.assertIsNone(s.observe(b'frame',10,stop))
