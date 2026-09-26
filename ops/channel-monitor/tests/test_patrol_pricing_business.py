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
