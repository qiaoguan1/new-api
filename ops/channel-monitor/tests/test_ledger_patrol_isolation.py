import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


patrol = load("ledger_patrol", "patrol_repair.py")
collector = load("ledger_collector", "fetch-upstream-balance.py")


class LedgerIsolationTests(unittest.TestCase):
    def test_large_ledger_uses_scoped_cap_and_other_artifacts_remain_bounded(self):
        now = 1791260000
        day = patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now, patrol.BEIJING))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "upstream-balance-ledger.json"
            payload = json.dumps({"days": {day: {"codeplan": {"collection_status": "complete"}}}})
            path.write_bytes(payload.encode() + b" " * (patrol.MAX_JSON_BYTES + 1))
            item = {"id": "ledger", "kind": "artifact", "artifact_type": "ledger", "path": str(path), "repair_action": "run.fetch_upstream_balance"}
            checks = patrol.PatrolChecks(patrol.CommandRunner())
            self.assertEqual(checks.evaluate(item, now).status, "healthy")
            bounded = checks.evaluate(item | {"artifact_type": "audit"}, now)
            self.assertEqual(bounded.code, "json_input_too_large")
            self.assertIsNone(bounded.repair_action)

    def test_invalid_or_oversized_reader_never_triggers_recollection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ledger.json"
            path.write_text("not json")
            item = {"id": "ledger", "kind": "artifact", "artifact_type": "ledger", "path": str(path), "repair_action": "run.fetch_upstream_balance"}
            runner = mock.Mock()
            result = patrol.PatrolChecks(runner).evaluate(item, 1791260000)
            self.assertEqual(result.code, "json_input_invalid")
            patrol.RepairCoordinator(runner, {"max_actions_per_run": 2}).repair([result], {}, lambda _: result, now=1791260000)
            runner.run_action.assert_not_called()
            path.write_bytes(b" " * 100)
            with mock.patch.object(patrol, "MAX_LEDGER_BYTES", 64):
                result = patrol.PatrolChecks(runner).evaluate(item, 1791260000)
            self.assertEqual(result.code, "json_input_too_large")
            self.assertIsNone(result.repair_action)

    def test_valid_partial_summary_warns_without_full_repair_and_mismatch_falls_back(self):
        now = 1791260000
        day = patrol.expected_business_day(patrol.datetime.datetime.fromtimestamp(now, patrol.BEIJING))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "upstream-balance-ledger.json"
            path.write_text(json.dumps({"days": {day: {"a": {"collection_status": "complete"}, "b": {"collection_status": "incomplete"}}}}))
            metadata = path.stat()
            summary = {"date": day, "source_bytes": metadata.st_size, "source_mtime_ns": metadata.st_mtime_ns, "complete": 1, "incomplete": 1, "skipped_manual": 2, "skipped_paused": 1}
            small = path.with_name("upstream-balance-health.json")
            small.write_text(json.dumps(summary))
            item = {"id": "ledger", "kind": "artifact", "artifact_type": "ledger", "path": str(path), "repair_action": "run.fetch_upstream_balance"}
            checks = patrol.PatrolChecks(patrol.CommandRunner())
            result = checks.evaluate(item, now)
            self.assertEqual((result.status, result.code), ("warning", "ledger_partial"))
            self.assertIsNone(result.repair_action)
            small.write_text(json.dumps(summary | {"source_mtime_ns": 0, "incomplete": 0}))
            self.assertEqual(checks.evaluate(item, now).code, "ledger_partial")

    def run_collector(self, directory, day, ledger, failing=None):
        root = pathlib.Path(directory)
        path = root / "ledger.json"
        path.write_text(json.dumps(ledger))
        credentials = {slug: {"username": "private", "password": "private"} for slug in ["a", "b", "0809", "toonflow", "jojocode"]}
        def collect(slug, *_):
            if slug == failing:
                raise RuntimeError("upstream unavailable")
            return {"collection_status": "complete", "actual_log_complete": True, "day_log_rows": 1, "day_log_cost_cny": 0.1}
        read = collector.read_json
        def config(p, default):
            fixture = {collector.CRED_PATH: credentials, collector.UPSTREAMS_PATH: [], collector.REVIEW_POLICY_PATH: {"policy": {"manual_weekly_providers": ["0809", "toonflow"], "jojo": "paused_until_operator_requests_resume"}}}
            return fixture[p] if p in fixture else read(p, default)
        with mock.patch.object(collector, "LEDGER_PATH", path), mock.patch.object(collector, "read_json", side_effect=config), mock.patch.object(collector, "target_beijing_day", return_value=day), mock.patch.object(collector, "collect_bounded", side_effect=collect) as calls, contextlib.redirect_stdout(io.StringIO()):
            result = collector.main()
        return result, json.loads(path.read_text()), calls

    def test_manual_and_paused_do_not_login_or_replace_evidence(self):
        day = "2026-10-05"
        prior = {"collection_status": "complete", "day_log_cost_cny": 2, "operator_evidence": "screenshot"}
        with tempfile.TemporaryDirectory() as directory:
            code, ledger, calls = self.run_collector(directory, day, {"days": {day: {"0809": prior}}})
            self.assertEqual(code, 0)
            self.assertEqual(ledger["days"][day]["0809"], prior)
            self.assertEqual([call.args[0] for call in calls.call_args_list], ["a", "b"])
            self.assertIsNone(ledger["days"][day]["toonflow"]["day_log_cost_cny"])

    def test_one_upstream_failure_preserves_unaffected_results(self):
        day = "2026-10-05"
        with tempfile.TemporaryDirectory() as directory:
            code, ledger, calls = self.run_collector(directory, day, {"days": {}}, failing="a")
            self.assertEqual(code, 2)
            self.assertEqual(calls.call_count, 2)
            self.assertEqual(ledger["days"][day]["b"]["collection_status"], "complete")
            self.assertIsNone(ledger["days"][day]["a"]["day_log_cost_cny"])

    def test_corrupt_history_fails_before_network_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ledger.json"
            path.write_text("invalid")
            with mock.patch.object(collector, "LEDGER_PATH", path), mock.patch.object(collector, "collect_one") as collect, mock.patch.object(collector, "write_json") as write:
                with self.assertRaisesRegex(RuntimeError, "json_input_invalid"):
                    collector.main()
                collect.assert_not_called()
                write.assert_not_called()
            self.assertEqual(path.read_text(), "invalid")

    @unittest.skipUnless("fork" in collector.multiprocessing.get_all_start_methods(), "production worker isolation requires Linux/fork")
    def test_hung_provider_is_bounded_and_does_not_touch_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ledger.json"
            path.write_text('{"days":{}}')
            with mock.patch.object(collector, "LEDGER_PATH", path), mock.patch.object(collector, "collect_one", side_effect=lambda *_: time.sleep(10)):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "provider_collection_deadline"):
                    collector.collect_bounded("hung", {"username": "test", "password": "test"}, "https://example.invalid", {"days": {}}, "2026-10-05", deadline=.1)
                self.assertLess(time.monotonic()-started, 5.5)
            self.assertEqual(path.read_text(), '{"days":{}}')
