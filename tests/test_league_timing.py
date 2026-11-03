import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import App
from league_season import SeasonProgress
from league_timing import LeagueTiming
from registry import new_button


class TimingTests(unittest.TestCase):
    def test_full_games_average_and_persist_without_active_monotonic_clock(self):
        with tempfile.TemporaryDirectory() as root:
            timing=LeagueTiming(root)
            timing.start(100);self.assertFalse(timing.start(150))
            self.assertIn('현재 경기 02:00',timing.text(220))
            self.assertEqual(timing.finish(1000),(900,False))
            timing.start(1100);timing.finish(2300)
            self.assertIn('최근 2경기 평균 17:30',timing.text())
            restarted=LeagueTiming(root)
            self.assertIsNone(restarted.started)
            self.assertEqual(len(restarted.samples),2)

    def test_midgame_stop_and_reconnect_are_excluded_from_average(self):
        with tempfile.TemporaryDirectory() as root:
            timing=LeagueTiming(root)
            timing.start(100,partial=True);timing.finish(900)
            self.assertIn('평균 제외',timing.text());self.assertFalse(timing.samples)
            timing.start(1000);timing.cancel();self.assertIsNone(timing.finish(4000))
            timing.start(5000);timing.finish(5005)
            self.assertFalse(timing.samples)

    def test_recent_ten_and_dry_files_do_not_mix(self):
        with tempfile.TemporaryDirectory() as root:
            timing=LeagueTiming(root)
            for index in range(12):timing.start(index*1000);timing.finish(index*1000+60+index)
            self.assertEqual(len(timing.samples),10);self.assertEqual(timing.samples[0]['seconds'],62)
            dry=LeagueTiming(root,dry=True);self.assertFalse(dry.samples)
            dry.start(0);dry.finish(120)
            self.assertEqual(len(LeagueTiming(root).samples),10)
            self.assertEqual(len(LeagueTiming(root,dry=True).samples),1)

    def test_corrupt_history_and_save_failure_are_nonfatal(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'league_timing.json').write_text('{',encoding='utf-8')
            timing=LeagueTiming(root);self.assertFalse(timing.samples)
            timing.start(10)
            with patch.object(Path,'replace',side_effect=PermissionError()):timing.finish(1010)
            self.assertEqual(len(timing.samples),1)

    def test_runner_new_season_and_two_game_results_without_lobby_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            # Simulate the stale previous-season cache seen on the user's PC.
            season=SeasonProgress(root);season.lobby(144);season.lobby(144);season.playing();season.result()
            app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
            app.emit=Mock();app.events=queue.Queue();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
            buttons=[]
            for name in ('새로운리그의시작','nextmatch','포수리드','경기결과 다음'):
                button=new_button(name);button.update(enabled=True,template=name,kind='condition' if name=='포수리드' else 'normal')
                buttons.append(button)
            states=(['새로운리그의시작']*3+['nextmatch']*6+['포수리드']*3+['경기결과 다음']*3+['']*3
                    +['nextmatch']*3+['포수리드']*3+['경기결과 다음']*3)
            adb=Mock();adb.screenshot.side_effect=states+[RuntimeError('end of replay')]
            def matcher(path,*args,**kwargs):
                m=Mock();m.find.side_effect=lambda frame:(frame==path,(100,200),.99,(720,1280));return m
            cfg=dict(buttons=buttons,adb='adb',serial='127.0.0.1:5555',dry=False,
                     league_start='포수리드',league_result='경기결과 다음')
            with patch('app.ROOT',root),patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),\
                    patch('app.time.monotonic',side_effect=range(0,10000,5)),patch('diagnostics.write_stop_snapshot'):
                app.run_session(cfg)
            self.assertEqual(SeasonProgress(root).data['completed'],2)
            self.assertEqual(len(LeagueTiming(root).samples),2,'\n'.join(c.args[0] for c in app.emit.call_args_list))
            messages=list(app.events.queue)
            self.assertTrue(any(m[0]=='league_season' and '2/144' in m[1] for m in messages))
            self.assertTrue(any(m[0]=='league_time' and '최근 2경기 평균' in m[1] for m in messages))


if __name__=='__main__':unittest.main()
