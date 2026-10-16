from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from app import App
from registry import new_button
from progress import ProgressClicker,ProgressStore,default_progress,validate_progress


def fixtures():
    records=[]
    for key in ['pb','pitch']:
        record=new_button(key);record.update(id=key,enabled=True,template=key+'.png');records.append(record)
    config=default_progress()
    config.update(enabled=True,watch=['pb','pitch'],point=[450,500],size=[720,1280])
    return records,config


class ProgressTests(unittest.TestCase):
    def test_disabled_by_default(self):
        p=ProgressClicker(default_progress(),[])
        self.assertIsNone(p.observe({},100))

    def test_both_absent_delay_and_repeat_interval(self):
        records,config=fixtures();p=ProgressClicker(config,records)
        for t in [0,1,2]:self.assertIsNone(p.observe({},t))
        self.assertEqual(p.observe({},3),(450,500))
        self.assertIsNone(p.observe({},7.9))
        self.assertEqual(p.observe({},8),(450,500))

    def test_either_watch_image_resets_wait(self):
        for key in ['pb','pitch']:
            records,config=fixtures();p=ProgressClicker(config,records)
            for t in [0,1,2]:p.observe({},t)
            self.assertIsNone(p.observe({key:True},3))
            for t in [4,5,6]:self.assertIsNone(p.observe({},t))
            self.assertEqual(p.observe({},7),(450,500))

    def test_other_activity_prevents_taps_and_resets_timer(self):
        records,config=fixtures();p=ProgressClicker(config,records)
        for t in range(10):self.assertIsNone(p.observe({},t,blocked=True))
        for t in [10,11,12]:self.assertIsNone(p.observe({},t))
        self.assertEqual(p.observe({},13),(450,500))

    def test_three_samples_required_even_after_long_time(self):
        records,config=fixtures();p=ProgressClicker(config,records)
        self.assertIsNone(p.observe({},0))
        self.assertIsNone(p.observe({},100))
        self.assertEqual(p.observe({},101),(450,500))

    def test_wrong_resolution_and_bad_settings_rejected(self):
        records,config=fixtures();p=ProgressClicker(config,records)
        with self.assertRaises(ValueError):p.check_size((1080,1920))
        for change in [dict(watch=['pb','pb']),dict(watch=['pb','missing']),dict(point=[720,0]),dict(delay=0),dict(interval=float('nan'))]:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_progress(dict(config,**change),records)

    def test_settings_round_trip(self):
        records,config=fixtures()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=ProgressStore(folder);store.save(config,records)
            self.assertEqual(store.load(),config)

    def run_sequence(self,dry):
        records,config=fixtures()
        app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
        adb=Mock();adb.screenshot.side_effect=list(range(13))+[RuntimeError('offline')]
        def matcher(path,*args):
            obj=Mock()
            obj.find.side_effect=lambda frame:(path=='pb.png' and frame>=9,(100,200),.99 if path=='pb.png' and frame>=9 else .1,(720,1280))
            return obj
        cfg=dict(buttons=records,rules=[],progress=config,dry=dry,adb='adb',serial='device')
        with patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),patch('app.time.monotonic',side_effect=range(100)):
            app.run(cfg)
        return adb,app

    def test_runner_fallback_stops_when_playball_returns(self):
        adb,app=self.run_sequence(False)
        self.assertEqual([c.args[0] for c in adb.tap.call_args_list],[(450,500),(450,500),(100,200)])
        self.assertTrue(any('보조 탭' in c.args[0] for c in app.emit.call_args_list))

    def test_runner_dry_run_never_sends_fallback_or_normal_taps(self):
        adb,app=self.run_sequence(True)
        adb.tap.assert_not_called()
        self.assertTrue(any('모의 보조 탭' in c.args[0] for c in app.emit.call_args_list))
