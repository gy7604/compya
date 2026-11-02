import tempfile
import unittest
from pathlib import Path
from league_rewards import choose_reward,RewardPolicy
from engine import SafetyStop


def card(kind='player',new=False,special=False):return dict(kind=kind,new=new,special=special)


class RewardTests(unittest.TestCase):
    def test_unreadable_player_type_is_not_discarded_or_selected_over_a_special(self):
        unknown=dict(card(),rarity_known=False)
        for remaining in (0,1,4):
            with self.subTest(remaining=remaining),self.assertRaises(SafetyStop):choose_reward([unknown,card('craft')],remaining)
        self.assertEqual(choose_reward([unknown,card(new=True)],4),('pick',1))
        self.assertEqual(choose_reward([unknown,card(special=True)],4),('pick',1))

    def test_new_then_special_skip_reroll(self):
        cards=[card(special=True),card(new=True),card('craft'),card('enhance')]
        self.assertEqual(choose_reward(cards,4),('pick',1))
        self.assertEqual(choose_reward(cards[:1]+cards[2:],4),('pick',0))

    def test_reroll_until_exhausted_then_player_craft_enhance(self):
        cards=[card('enhance'),card('craft'),card()]
        self.assertEqual(choose_reward(cards,2),('reroll',None))
        self.assertEqual(choose_reward(cards,0),('pick',2))
        self.assertEqual(choose_reward(cards[:2],0),('pick',1))
        self.assertEqual(choose_reward(cards[:1],0),('pick',0))
        with self.assertRaises(SafetyStop):choose_reward(cards,None)

    def test_claim_requires_selected_target_and_durable_intent(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'state.json';m=RewardPolicy(p)
            view=dict(lobby=True,cards=[card(new=True),card('craft')],remaining=4,board='a',selected=None)
            self.assertIsNone(m.observe(view,0));self.assertEqual(m.observe(view,1),('pick',0))
            self.assertIsNone(m.observe(view,2))
            m=RewardPolicy(p);view['selected']=0
            self.assertIsNone(m.observe(view,3));self.assertEqual(m.observe(view,4),('choose',None))
            m=RewardPolicy(p)
            self.assertIsNone(m.observe(dict(dialog=True),5));self.assertEqual(m.observe(dict(dialog=True),6),('confirm',None))
            self.assertIsNone(m.observe(dict(dialog=True),7))
            m=RewardPolicy(p);self.assertIsNone(m.observe(dict(dialog=True),8));self.assertIsNone(m.observe(dict(dialog=True),9))
            m.observe(dict(complete=True),10);self.assertEqual(m.data,{})

    def test_pending_reroll_is_not_repeated_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'state.json';m=RewardPolicy(p)
            view=dict(lobby=True,cards=[card()],remaining=2,board='a',selected=None)
            m.observe(view,0);self.assertEqual(m.observe(view,1),('reroll',None))
            m=RewardPolicy(p)
            for t in range(2,6):self.assertIsNone(m.observe(view,t))
            view.update(remaining=1,board='b')
            m.observe(view,6);self.assertEqual(m.observe(view,7),('reroll',None))
            view.update(remaining=0,board='c');m.observe(view,8)
            self.assertEqual(m.observe(view,9),('pick',0))

    def test_unknown_dialog_never_confirms(self):
        with tempfile.TemporaryDirectory() as root:
            m=RewardPolicy(Path(root)/'state.json');m.observe(dict(dialog=True),0)
            with self.assertRaises(SafetyStop):m.observe(dict(dialog=True),1)

    def test_changed_card_is_not_claimed(self):
        with tempfile.TemporaryDirectory() as root:
            m=RewardPolicy(Path(root)/'state.json')
            view=dict(lobby=True,cards=[card(new=True)],remaining=1,board='a',selected=None)
            m.observe(view,0);m.observe(view,1)
            view.update(cards=[card()],selected=0);m.observe(view,2)
            with self.assertRaises(SafetyStop):m.observe(view,3)
