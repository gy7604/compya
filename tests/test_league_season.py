import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from engine import SafetyStop
from league_season import SeasonProgress,RoundReader


class SeasonTests(unittest.TestCase):
    def test_round_is_current_game_not_completed_count_and_result_is_once(self):
        with tempfile.TemporaryDirectory() as root:
            season=SeasonProgress(root)
            self.assertIn('확인 대기',season.text())
            self.assertFalse(season.lobby(28));self.assertTrue(season.lobby(28))
            self.assertIn('27/144',season.text());self.assertIn('다음 28',season.text())
            self.assertTrue(season.playing());self.assertIn('28번째 경기 진행 중',season.text())
            self.assertTrue(season.result());self.assertFalse(season.result())
            self.assertIn('28/144',season.text())
            self.assertFalse(season.lobby(28));self.assertEqual(season.data['completed'],28)

    def test_restart_shows_last_known_without_counting_until_lobby_reverified(self):
        with tempfile.TemporaryDirectory() as root:
            season=SeasonProgress(root);season.lobby(40);season.lobby(40);season.playing()
            resumed=SeasonProgress(root)
            self.assertIn('마지막 확인',resumed.text());self.assertFalse(resumed.result())
            self.assertFalse(resumed.lobby(40));self.assertTrue(resumed.lobby(40))
            resumed.playing();resumed.result();self.assertEqual(resumed.data['completed'],40)

    def test_final_round_and_new_season_and_external_jump(self):
        with tempfile.TemporaryDirectory() as root:
            season=SeasonProgress(root);season.lobby(144);season.lobby(144)
            season.playing();season.result();self.assertIn('144/144경기 완료',season.text())
            season.lobby(1);season.lobby(1);self.assertEqual(season.data['completed'],0)
            season.lobby(18);season.lobby(18);self.assertEqual(season.data['completed'],17)

    def test_invalid_or_disagreeing_round_never_changes_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            season=SeasonProgress(root)
            for value in (0,145,True,'28',None):self.assertFalse(season.lobby(value))
            for value in (28,29,28,29):self.assertFalse(season.lobby(value))
            self.assertIsNone(season.data)

    def test_dry_records_are_separate_and_ranking_label_preserves_state(self):
        with tempfile.TemporaryDirectory() as root:
            real=SeasonProgress(root);real.lobby(10);real.lobby(10)
            dry=SeasonProgress(root,dry=True);dry.lobby(2);dry.lobby(2)
            self.assertEqual(SeasonProgress(root).data['round'],10)
            self.assertIn('랭킹 진행 중',real.text(True))

    def test_corrupt_cache_and_write_failure_do_not_stop_gameplay(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'league_season.json').write_text('{',encoding='utf-8')
            season=SeasonProgress(root);self.assertIsNone(season.data)
            season.lobby(3)
            with patch.object(Path,'replace',side_effect=PermissionError()):self.assertTrue(season.lobby(3))
            self.assertIn('기록 저장 대기',season.text())


class RoundReaderTests(unittest.TestCase):
    def reader(self):
        reader=RoundReader(Path(__file__).resolve().parents[1])
        reader.reader=Mock();reader.reader.read.side_effect=[28,28]
        reader.title=Mock();reader.title.find.return_value=(True,(360,580),1.,(720,1280))
        reader.label=Mock();reader.label.find.return_value=(True,(335,636),1.,(720,1280))
        reader.label.template=np.zeros((29,80),np.uint8)
        return reader

    def png(self):return cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()

    def test_two_variants_and_no_device_input(self):
        reader=self.reader();self.assertEqual(reader.observe(self.png(),1,threading.Event()),28)
        self.assertEqual(reader.reader.read.call_count,2)
        self.assertIsNone(reader.observe(self.png(),1.5,threading.Event()))

    def test_ocr_uncertain_failure_and_out_of_range_are_nonfatal(self):
        for values in ([28,29],[145,145],SafetyStop('OCR failed')):
            reader=self.reader();reader.reader.read.side_effect=values
            self.assertIsNone(reader.observe(self.png(),1,threading.Event()))

    def test_stop_or_wrong_screen_never_runs_ocr(self):
        reader=self.reader();stop=threading.Event();stop.set()
        self.assertIsNone(reader.observe(self.png(),1,stop));reader.reader.read.assert_not_called()
        reader.title.find.return_value=(False,(0,0),0.,(720,1280))
        self.assertIsNone(reader.observe(self.png(),2,threading.Event()));reader.reader.read.assert_not_called()
        reader.title.find.return_value=(True,(0,0),1.,(1080,1920))
        self.assertIsNone(reader.observe(self.png(),3,threading.Event()));reader.reader.read.assert_not_called()
