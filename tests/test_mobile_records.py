import json
import tempfile
import unittest
from pathlib import Path
from mobile_records import RunTelemetry,RecordReader
from league_season import SeasonProgress


class RecordTests(unittest.TestCase):
    def test_manual_stop_and_dry_runs_never_become_error_records(self):
        with tempfile.TemporaryDirectory() as root:
            run=RunTelemetry(root);run.begin(False);run.expected('manual');run.failure('macro_error');run.finish()
            self.assertEqual(RecordReader(root).snapshot()['faults'],[])
            run.begin(True);run.failure('player_error');self.assertEqual(run.data['faults'],[])

    def test_crash_and_player_failure_are_durable_once_per_run_without_raw_log_text(self):
        with tempfile.TemporaryDirectory() as root:
            run=RunTelemetry(root);run.begin(False);run.failure('player_error');run.failure('macro_error');run.finish()
            self.assertEqual(len(RunTelemetry(root).data['faults']),1)
            run.begin(False);resumed=RunTelemetry(root)
            self.assertEqual(resumed.data['faults'][-1]['kind'],'unclean_exit')
            self.assertEqual(len(RunTelemetry(root).data['faults']),2)

    def test_planned_reboot_does_not_report_an_unclean_exit(self):
        with tempfile.TemporaryDirectory() as root:
            run=RunTelemetry(root);run.begin(False);run.expected('planned_reboot');run.finish()
            self.assertEqual(RunTelemetry(root).data['faults'],[])

    def test_144th_verified_result_is_retained_after_next_season_starts(self):
        with tempfile.TemporaryDirectory() as root:
            season=SeasonProgress(root);season.lobby(144);season.lobby(144);season.playing();season.result();season.new_season()
            facts=RecordReader(root).snapshot()['seasons'];self.assertEqual(len(facts),1);self.assertEqual(facts[0]['completed'],144)
            self.assertNotEqual(facts[0]['id'],season.data['season_id'])
            resumed=SeasonProgress(root);self.assertFalse(resumed.result());self.assertEqual(len(RecordReader(root).snapshot()['seasons']),1)

    def test_last_confirmed_and_dry_season_do_not_manufacture_a_completion(self):
        with tempfile.TemporaryDirectory() as root:
            season=SeasonProgress(root,dry=True);season.lobby(144);season.lobby(144);season.playing();season.result()
            self.assertEqual(RecordReader(root).snapshot()['seasons'],[])

    def test_reader_exports_only_aggregates_and_preserves_uncounted_results(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'ranking_daily.json').write_text(json.dumps({'days':{'2026-11-04':dict(status='done',completed=2,results={'1':[3,1,1],'2':None},last_payment={'private':'do not transmit'})}}))
            Path(root,'notifications.json').write_text('{"topic":"private-channel"}')
            result=RecordReader(root).snapshot();ranking=result['ranking'][0]
            self.assertEqual((ranking['wins'],ranking['draws'],ranking['losses'],ranking['missing']),(3,1,1,1))
            self.assertNotIn('private',json.dumps(result))
            Path(root,'ranking_daily.json').write_text('{')
            self.assertFalse(RecordReader(root).snapshot()['quality'])
