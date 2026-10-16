import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import App
from registry import new_button
from ranking import *

KEYS=['lobby','refresh','points','paid','series','disabled','proceed','result','confirm']
def fixture():
    records=[]
    for i,key in enumerate(KEYS):
        r=new_button(key);r.update(id=key,enabled=True,kind='condition',template=key+'.png',roi=[0,0,720,1280],click_mode='fixed',click_point=[10+i,20],click_size=[720,1280]);records.append(r)
    config=dict(default_ranking(),enabled=True,**{key:key for key in KEYS})
    return records,config

def visible(*keys):return dict.fromkeys(keys,True)

class RankingTests(unittest.TestCase):
    def machine(self,target=2):
        records,config=fixture();return RankingMachine(config,records,target)
    def frames(self,m,found,start):return [m.observe(found,start+i) for i in range(3)]
    def begin(self,m,start=0):
        self.assertEqual(self.frames(m,visible('lobby','series','refresh'),start),[None,None,'series'])
        self.assertEqual(self.frames(m,visible('proceed'),start+3),[None,None,'proceed'])
        self.frames(m,{},start+6)
        self.assertEqual(m.phase,'playing')
    def test_active_series_has_priority_even_with_free_or_paid_refresh(self):
        for refresh in ['refresh','paid']:
            m=self.machine()
            self.assertEqual(self.frames(m,visible('lobby','series',refresh),0),[None,None,'series'])
    def test_free_refresh_requires_positive_disabled_evidence(self):
        m=self.machine()
        for t in range(9):self.assertIsNone(m.observe(visible('lobby','refresh'),t))
        self.assertEqual(self.frames(m,visible('lobby','refresh','disabled'),9),[None,None,'refresh'])
        for t in range(12,20):self.assertIsNone(m.observe(visible('lobby','refresh','disabled'),t))
        self.assertEqual(self.frames(m,visible('lobby','series','refresh'),20),[None,None,'series'])
    def test_ambiguous_active_disabled_never_refreshes(self):
        m=self.machine()
        for t in range(12):self.assertIsNone(m.observe(visible('lobby','series','disabled','refresh'),t))
    def test_partial_active_evidence_vetoes_refresh_immediately(self):
        m=self.machine()
        m.observe(visible('lobby','disabled','refresh'),0);m.observe(visible('lobby','disabled','refresh'),1)
        self.assertIsNone(m.observe(visible('lobby','disabled','refresh','series'),2))
    def test_paid_refresh_never_clicked_and_disabled_waits_forever(self):
        m=self.machine()
        for t in [0,1,2,86400]:self.assertIsNone(m.observe(visible('lobby','paid','disabled'),t))
        self.assertEqual(m.phase,'lobby');self.assertFalse(m.finished)
    def test_result_counts_once_and_stops_before_confirmation_at_target(self):
        m=self.machine(1);self.begin(m)
        self.frames(m,visible('lobby','series'),9);self.assertEqual(m.completed,0)
        self.frames(m,visible('result','confirm'),12)
        self.assertTrue(m.finished);self.assertEqual(m.completed,1)
        for t in range(15,25):self.assertIsNone(m.observe(visible('result','confirm'),t))
        self.assertEqual(m.completed,1)
    def test_result_confirmation_return_to_disabled_lobby_and_repeat(self):
        m=self.machine(2);self.begin(m)
        self.frames(m,visible('result','confirm'),9);self.assertEqual(m.completed,1)
        self.assertEqual(self.frames(m,visible('result','confirm'),12),[None,None,'confirm'])
        for t in range(15,20):self.assertIsNone(m.observe(visible('result','confirm'),t))
        self.frames(m,visible('lobby','disabled','refresh'),20)
        self.assertEqual(m.phase,'lobby')
        self.assertEqual(self.frames(m,visible('lobby','disabled','refresh'),23),[None,None,'refresh'])
        self.frames(m,visible('lobby','series'),26)
        self.frames(m,visible('proceed'),29)
        self.frames(m,{},32)
        self.frames(m,visible('result','confirm'),35)
        self.assertTrue(m.finished);self.assertEqual(m.completed,2)
    def test_validation_and_settings(self):
        records,cfg=fixture()
        for change in [dict(disabled=''),dict(refresh='series'),dict(series='missing')]:
            with self.assertRaises(ValueError):validate_ranking(dict(cfg,**change),records)
        validate_ranking(dict(cfg,paid=''),records)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=RankingStore(folder);store.save(cfg,records);self.assertEqual(store.load(),cfg)
        records[0]['kind']='normal'
        with self.assertRaises(ValueError):validate_ranking(cfg,records)
    def runner(self,dry):
        records,cfg=fixture();app=App.__new__(App);app.stop=threading.Event();app.stop.wait=Mock(return_value=False)
        app.emit=Mock();app.registry=Mock();app.registry.resolve.side_effect=lambda p:p
        adb=Mock();adb.screenshot.side_effect=range(100)
        def found(key,frame):
            if frame<3:return key in ['lobby','series','refresh']
            if frame<6:return key=='proceed'
            if frame<9:return False
            return key in ['result','confirm']
        def matcher(path,*args,**kwargs):
            key=path.removesuffix('.png');obj=Mock();obj.find.side_effect=lambda frame:(found(key,frame),(100,200),.99,(720,1280));return obj
        with patch('app.ADB',return_value=adb),patch('vision.Matcher',side_effect=matcher),patch('app.time.monotonic',side_effect=range(1000)):
            app.run(dict(buttons=records,rules=[],ranking=cfg,games=1,dry=dry,adb='adb',serial='device'))
        return app,adb
    def test_runner_stops_at_target_and_only_taps_series_and_proceed(self):
        app,adb=self.runner(False)
        self.assertEqual([c.args[0] for c in adb.tap.call_args_list],[(14,20),(16,20)])
        self.assertTrue(any('목표 횟수 완료' in c.args[0] for c in app.emit.call_args_list))
        self.assertFalse(any('안전 중지' in c.args[0] for c in app.emit.call_args_list))
    def test_dry_run_never_taps(self):
        app,adb=self.runner(True);adb.tap.assert_not_called()
        self.assertTrue(any('모의 랭킹 탭' in c.args[0] for c in app.emit.call_args_list))

    def test_points_refresh_only_after_disabled_and_never_when_star_visible(self):
        m=self.machine()
        self.assertEqual(self.frames(m,visible('lobby','series','points'),0),[None,None,'series'])
        m=self.machine()
        for t in range(3):self.assertIsNone(m.observe(visible('lobby','points'),t))
        self.assertEqual(self.frames(m,visible('lobby','disabled','points'),3),[None,None,'points'])
        m=self.machine()
        for t in range(6):self.assertIsNone(m.observe(visible('lobby','disabled','points','paid'),t))
    def test_free_refresh_precedes_points_and_points_can_be_disabled(self):
        m=self.machine()
        self.assertEqual(self.frames(m,visible('lobby','disabled','refresh','points'),0),[None,None,'refresh'])
        records,cfg=fixture();cfg['points']='';m=RankingMachine(cfg,records,1)
        for t in range(6):self.assertIsNone(m.observe(visible('lobby','disabled','points'),t))
    def test_points_template_requires_search_area(self):
        records,cfg=fixture();next(r for r in records if r['id']=='points')['roi']=None
        with self.assertRaises(ValueError):validate_ranking(cfg,records)

    def reward_machine(self,target=2):
        records,cfg=fixture()
        for key in ['reward','reward_confirm']:
            r=new_button(key);r.update(id=key,kind='condition',template=key+'.png',enabled=True,roi=[500,1000,200,200],click_mode='fixed',click_point=[600,1100],click_size=[720,1280]);records.append(r);cfg[key]=key
        return RankingMachine(cfg,records,target)
    def test_reward_preempts_series_and_refresh(self):
        m=self.reward_machine()
        self.assertEqual(self.frames(m,visible('lobby','series','refresh','reward'),0),[None,None,'reward'])
        for t in range(3,12):self.assertIsNone(m.observe(visible('lobby','series','reward'),t))
        self.assertEqual(m.phase,'reward_confirm')
    def test_reward_popup_close_and_marker_reset_before_next_game(self):
        m=self.reward_machine()
        self.frames(m,visible('lobby','reward'),0)
        self.assertEqual(self.frames(m,visible('reward_confirm'),3),[None,None,'reward_confirm'])
        for t in range(6,12):self.assertIsNone(m.observe(visible('lobby','reward','series'),t))
        self.assertEqual(m.phase,'reward_return')
        self.frames(m,visible('lobby','series'),12);self.assertEqual(m.phase,'lobby')
        self.assertEqual(self.frames(m,visible('lobby','series'),15),[None,None,'series'])
    def test_last_batch_returns_and_claims_reward_before_stopping(self):
        m=self.reward_machine(1);self.begin(m)
        self.frames(m,visible('result','confirm'),9);self.assertFalse(m.finished)
        self.assertEqual(self.frames(m,visible('result','confirm'),12),[None,None,'confirm'])
        self.frames(m,visible('lobby','reward','series'),15)
        self.assertEqual(self.frames(m,visible('lobby','reward','series'),18),[None,None,'reward'])
        self.frames(m,visible('reward_confirm'),21)
        self.frames(m,visible('lobby','series'),24)
        self.frames(m,visible('lobby','series'),27)
        self.assertTrue(m.finished);self.assertEqual(m.completed,1)
    def test_last_batch_without_ready_reward_stops_in_lobby(self):
        m=self.reward_machine(1);self.begin(m)
        self.frames(m,visible('result','confirm'),9)
        self.frames(m,visible('result','confirm'),12)
        self.frames(m,visible('lobby','series'),15)
        self.frames(m,visible('lobby','series'),18)
        self.assertTrue(m.finished)
    def test_reward_requires_pair_and_specific_roi(self):
        m=self.reward_machine();records=list(m.records.values());cfg=m.config
        with self.assertRaises(ValueError):validate_ranking(dict(cfg,reward_confirm=''),records)
        next(r for r in records if r['id']=='reward')['roi']=None
        with self.assertRaises(ValueError):validate_ranking(cfg,records)
