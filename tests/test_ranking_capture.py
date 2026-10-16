import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from registry import Registry,new_button
from ranking_capture import save_rank_image,save_rank_point

class CaptureTests(unittest.TestCase):
    def app(self,folder):return SimpleNamespace(registry=Registry(folder),records=[])
    def test_register_replace_and_point_round_trip(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            app=self.app(folder)
            r=save_rank_image(app,None,'활성 연속 경기',Path(folder)/'capture.png',[0,0,100,100],[720,1280])
            self.assertEqual(r['kind'],'condition');self.assertTrue(r['enabled'])
            r=save_rank_point(app,r['id'],[35,40],[720,1280]);self.assertEqual(r['click_mode'],'fixed')
            revised=save_rank_image(app,r['id'],'교체',Path(folder)/'new.png',[1,2,100,100],[720,1280])
            self.assertEqual(revised['id'],r['id']);self.assertEqual(len(app.records),1)
            self.assertEqual(app.registry.load({}),app.records)
            self.assertEqual(revised['click_point'],[35,40])
    def test_shared_normal_button_is_copied_and_resolution_change_clears_coordinates(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            app=self.app(folder);normal=new_button('PLAY BALL');app.records=[normal]
            r=save_rank_image(app,normal['id'],'진행',Path(folder)/'a.png',[0,0,100,100],[720,1280])
            self.assertNotEqual(r['id'],normal['id']);self.assertEqual(app.records[0],normal)
            save_rank_point(app,r['id'],[30,40],[720,1280])
            r=save_rank_image(app,r['id'],'진행',Path(folder)/'b.png',[0,0,100,100],[1080,1920])
            self.assertEqual(r['click_mode'],'center')
    def test_bad_resolution_and_failed_save_leave_records_unchanged(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            app=self.app(folder);r=save_rank_image(app,None,'진행',Path(folder)/'a.png',[0,0,100,100],[720,1280])
            with self.assertRaises(ValueError):save_rank_point(app,r['id'],[20,30],[1080,1920])
            old=app.records;app.registry.save=Mock(side_effect=OSError('disk'))
            with self.assertRaises(OSError):save_rank_image(app,None,'새 항목',Path(folder)/'b.png',[0,0,100,100],[720,1280])
            self.assertIs(app.records,old)

    def test_existing_rule_step_keeps_its_role_when_replaced(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            app=self.app(folder);record=new_button('규칙 단계');record['kind']='step';app.records=[record]
            revised=save_rank_image(app,record['id'],'교체',Path(folder)/'new.png',[0,0,100,100],[720,1280])
            self.assertEqual(revised['kind'],'step');self.assertEqual(revised['id'],record['id'])
