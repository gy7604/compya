import tempfile
import unittest
from pathlib import Path
from ranking import RankingStore,RankingMachine,default_ranking,validate_ranking

class DraftTests(unittest.TestCase):
    def test_incomplete_enabled_and_star_enabled_settings_save_and_restore(self):
        config=default_ranking();config.update(enabled=True,allow_star=True,lobby='known',reward='partial')
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=RankingStore(folder);store.save(config,[]);self.assertEqual(store.load(),config)
        with self.assertRaisesRegex(ValueError,'등록'):RankingMachine(config,[],1)
    def test_start_names_missing_fields_instead_of_blocking_save(self):
        config=default_ranking();config['enabled']=True
        with self.assertRaises(ValueError) as error:validate_ranking(config,[])
        self.assertIn('즉시 갱신 팝업 제목',str(error.exception));self.assertIn('활성 연속 경기',str(error.exception))
    def test_invalid_shape_does_not_replace_previous_draft(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            store=RankingStore(folder);old=default_ranking();store.save(old,[])
            for change in [dict(enabled='yes'),dict(points=[]),dict(allow_star=1)]:
                with self.assertRaises(ValueError):store.save(dict(old,**change),[])
                self.assertEqual(store.load(),old)
