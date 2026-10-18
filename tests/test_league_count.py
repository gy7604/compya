import unittest
from league_count import LeagueCounter

class LeagueCounterTests(unittest.TestCase):
    def test_initial_result_and_persistent_result_do_not_count(self):
        c=LeagueCounter('play','result')
        for _ in range(20):self.assertFalse(c.observe({'result':True}))
        self.assertEqual(c.completed,0)
    def test_two_games_and_no_duplicate_from_result_flicker(self):
        c=LeagueCounter('play','result')
        for game in range(2):
            for _ in range(2):c.observe({'play':True})
            self.assertFalse(c.observe({'result':True}))
            self.assertTrue(c.observe({'result':True}))
            for found in [{'result':True},{},{'result':True},{},{},{'result':True}]*5:
                self.assertFalse(c.observe(found))
            self.assertEqual(c.completed,game+1)
    def test_transient_play_or_result_is_not_enough(self):
        c=LeagueCounter('play','result')
        c.observe({'play':True});c.observe({})
        for _ in range(3):self.assertFalse(c.observe({'result':True}))
        c.observe({'play':True});c.observe({'play':True})
        self.assertFalse(c.observe({'result':True}));c.observe({})
        self.assertEqual(c.completed,0)
    def test_missing_or_same_images_disable_count(self):
        for start,result in [('', 'r'),('s',''),('x','x')]:
            c=LeagueCounter(start,result)
            for _ in range(5):self.assertFalse(c.observe({'s':True,'r':True,'x':True}))
