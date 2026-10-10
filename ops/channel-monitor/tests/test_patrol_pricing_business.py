import importlib.util
import pathlib
import sys
import unittest
import tempfile
import json

PATH = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'patrol_repair.py'
spec = importlib.util.spec_from_file_location('patrol_business', PATH)
patrol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patrol
spec.loader.exec_module(patrol)


class PricingBusinessTests(unittest.TestCase):
    def test_video_run_missing_is_distinct_from_executed_worker_failure(self):
        now = 1791595200
        day = patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now, patrol.BEIJING))
        for rows, expected in (
            ([{'date': '2026-10-07', 'generated_at': now-86400}], 'pricing_run_missing'),
            ([{'date': day, 'generated_at': now, 'status': 'failed'}], 'scheduled_run_failed'),
            ([{'date': day, 'generated_at': now, 'error': 'sanitized_failure'}], 'scheduled_run_failed'),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / 'video-runs.json'
                path.write_text(json.dumps({'runs': rows}))
                result = patrol.PatrolChecks(patrol.CommandRunner()).evaluate(
                    {'id': 'video-pricing', 'kind': 'artifact', 'path': str(path),
                     'artifact_type': 'video_pricing', 'repair_action': 'run.scan_daily_audit'}, now)
                self.assertEqual((result.status, result.code), ('failed', expected))
                if expected == 'pricing_run_missing':
                    self.assertIsNone(result.repair_action)

    def test_future_or_dry_run_does_not_hide_missing_current_business_day(self):
        now = 1791595200
        day = patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now, patrol.BEIJING))
        future = (patrol.datetime.date.fromisoformat(day) + patrol.datetime.timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'video-runs.json'
            path.write_text(json.dumps({'runs': [
                {'date': future, 'generated_at': now, 'status': 'complete'},
                {'date': day, 'generated_at': now, 'status': 'complete', 'dry_run': True},
            ]}))
            result = patrol.PatrolChecks(patrol.CommandRunner()).evaluate(
                {'id': 'video-pricing', 'kind': 'artifact', 'path': str(path),
                 'artifact_type': 'video_pricing'}, now)
            self.assertEqual((result.status, result.code), ('failed', 'pricing_run_missing'))

    def test_video_unknown_or_empty_execution_is_not_healthy_and_legacy_rows_remain_valid(self):
        now = 1791595200
        day = patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now, patrol.BEIJING))
        for fields, expected in (
            ({'status': 'unknown', 'decisions': [{'action': 'apply'}]}, ('warning', 'pricing_execution_unknown')),
            ({'status': 'complete', 'decisions': []}, ('warning', 'pricing_no_evaluations')),
            ({'status': 'complete', 'decisions': [{}]}, ('warning', 'pricing_evaluations_invalid')),
            ({'status': 'complete', 'decisions': [{'action': 'invalid'}]}, ('warning', 'pricing_evaluations_invalid')),
            ({'status': 'complete', 'decisions': {'action': 'apply'}}, ('warning', 'pricing_evaluations_invalid')),
            ({'status': 'complete', 'applied': None, 'decisions': [{'action': 'apply'}]}, ('warning', 'pricing_write_count_unknown')),
            ({'decisions': [{'action': 'apply'}]}, ('healthy', 'ok')),
        ):
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / 'video-runs.json'
                path.write_text(json.dumps({'runs': [{'date': day, 'generated_at': now, **fields}]}))
                result = patrol.PatrolChecks(patrol.CommandRunner()).evaluate(
                    {'id': 'video-pricing', 'kind': 'artifact', 'path': str(path),
                     'artifact_type': 'video_pricing', 'repair_action': 'run.scan_daily_audit'}, now)
                self.assertEqual((result.status, result.code), expected)
                if expected[0] == 'warning':
                    self.assertIsNone(result.repair_action)

    def test_price_write_evidence_preserves_unknown_and_truthful_legacy_unchanged(self):
        now = 1791595200
        day = patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now, patrol.BEIJING))
        for fields, expected in (
            ({'status': 'failed', 'database_write_attempted': True, 'applied': None}, (None, None)),
            ({'status': 'unknown', 'decisions': [{'action': 'apply'}]}, (None, None)),
            ({'status': 'complete', 'decisions': [{'action': 'invalid'}]}, (None, None)),
            ({'status': 'complete', 'changed': False, 'decisions': [{'action': 'apply'}]}, (0, 1)),
            ({'status': 'complete', 'applied': None, 'decisions': [{'action': 'apply'}]}, (None, 0)),
            ({'status': 'failed', 'database_write_attempted': False, 'applied': 0}, (0, None)),
        ):
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / 'video-runs.json'
                path.write_text(json.dumps({'runs': [{'date': day, 'generated_at': now, **fields}]}))
                result = patrol.PatrolChecks(patrol.CommandRunner()).evaluate(
                    {'id': 'video-pricing', 'kind': 'artifact', 'path': str(path),
                     'artifact_type': 'video_pricing'}, now)
                self.assertEqual((result.evidence['applied'], result.evidence['unchanged']), expected)

    def test_unknown_or_empty_run_is_not_healthy(self):
        self.assertEqual(patrol.pricing_business_health({'status':'unknown','decisions':[]})[0], 'warning')
        self.assertEqual(patrol.pricing_business_health({'status':'complete','decisions':[]})[0], 'warning')
    def test_artifact_excludes_dry_run_and_preserves_business_evidence(self):
        now=1790430000
        day=patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now,patrol.BEIJING))
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'runs.json'
            path.write_text(json.dumps({'runs':[
                {'date':day,'generated_at':now-10,'status':'complete','business_status':'partial','decisions':[{'action':'unchanged'},{'action':'skip','reason':'no_trusted_cost_evidence'}]},
                {'date':day,'generated_at':now,'status':'complete','dry_run':True,'decisions':[]}]}))
            result=patrol.PatrolChecks(patrol.CommandRunner())._artifact({'id':'pricing','path':str(path),'artifact_type':'generic_pricing','repair_action':'run.scan_daily_audit'},now)
            self.assertEqual(result.status,'warning')
            self.assertIsNone(result.repair_action)
            self.assertEqual(result.evidence['unchanged'],1)
            self.assertEqual(result.evidence['business_status'],'partial')

    def test_execution_complete_does_not_hide_blocked_business(self):
        self.assertEqual(patrol.pricing_business_health({'status':'complete','business_status':'blocked'}),
                         ('warning', 'pricing_business_blocked'))
        self.assertEqual(patrol.pricing_business_health({'status':'complete','business_status':'partial'}),
                         ('warning', 'pricing_business_partial'))

    def test_dry_run_is_not_a_completed_production_run(self):
        self.assertEqual(patrol.pricing_business_health({'status':'complete','dry_run':True}),
                         ('warning', 'pricing_dry_run_only'))

    def test_verified_unchanged_is_healthy_but_old_zero_skip_is_not(self):
        self.assertEqual(patrol.pricing_business_health({'status':'complete','decisions':[{'action':'unchanged'}]}), ('healthy','ok'))
        self.assertEqual(patrol.pricing_business_health({'status':'complete','decisions':[{'action':'skip','reason':'no_trusted_cost_evidence'}]}), ('warning','pricing_business_blocked'))
