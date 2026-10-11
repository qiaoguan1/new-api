"""Offline runner-boundary tests: no Docker, database or network calls."""
import tempfile
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import routing_runtime as runner


class RuntimeTests(unittest.TestCase):
    def test_scope_excludes_unfunded_unknown_video_and_disabled_image(self):
        self.assertEqual(len(runner.FIRST), 19)
        self.assertEqual(runner.DEPLETED, (23,))
        for value in (37, 42, 43, 46, 53, 54, 55, 70, 71, 72):
            self.assertNotIn(value, runner.FIRST + runner.DEPLETED)

    def test_operation_name_is_not_a_path(self):
        for value in ("../escape", "a" * 31, "A" * 32, "a/" * 16):
            with self.assertRaises(ValueError):
                runner.operation_path(value)

    def test_evidence_write_is_atomic_and_rejects_existing_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "proof.json"
            runner.write_json(target, {"status": "intent"})
            self.assertEqual(runner.read_json(target)["status"], "intent")
            self.assertFalse(target.with_suffix(".tmp").exists())
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(ValueError):
                    runner.write_json(target, {"status": "changed"})

    def test_reconciliation_never_replays_sql(self):
        for current, expected in (("before", "not_applied"), ("after", "target_verified"), ("other", "foreign_or_unknown")):
            self.assertEqual(runner.reconcile_hash(current, "before", "after"), expected)

    def test_affinity_calls_only_fixed_local_tls_endpoints(self):
        self.assertEqual(runner.AFFINITY_RULES, ("codex cli trace", "claude cli trace"))
        self.assertNotIn("all=true", runner.AFFINITY_PATH)

    def test_apply_durable_intent_precedes_only_one_sql_and_repeat_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = {'status': 'not_applied', 'outside_unchanged': True, 'native_same': True}
            after = dict(before, status='target_verified')
            def execute(_sql):
                self.assertEqual(runner.read_json(root / 'state.json')['status'], 'apply_intent')
                return 'COMMIT'
            with patch.object(runner, 'load_manifest', return_value={'plan': {}}), \
                 patch.object(runner, 'evidence', return_value={}), \
                 patch.object(runner, 'reconcile', side_effect=[before, after]), \
                 patch.object(runner.planner, 'render_apply_sql', return_value='reviewed SQL'), \
                 patch.object(runner, 'psql', side_effect=execute) as executor:
                self.assertEqual(runner.apply(root)['status'], 'target_verified')
                with self.assertRaises(ValueError):
                    runner.apply(root)
                self.assertEqual(executor.call_count, 1)

    def test_unknown_executor_result_is_journaled_without_replay_or_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = {'status': 'not_applied', 'outside_unchanged': True, 'native_same': True}
            with patch.object(runner, 'load_manifest', return_value={'plan': {}}), \
                 patch.object(runner, 'evidence', return_value={}), \
                 patch.object(runner, 'reconcile', return_value=before) as reader, \
                 patch.object(runner.planner, 'render_apply_sql', return_value='reviewed SQL'), \
                 patch.object(runner, 'psql', side_effect=TimeoutError) as executor:
                with self.assertRaises(RuntimeError):
                    runner.apply(root)
                self.assertEqual(runner.read_json(root / 'state.json')['status'], 'outcome_unknown')
                self.assertEqual(executor.call_count, 1)
                self.assertEqual(reader.call_count, 1)
                with self.assertRaises(ValueError):
                    runner.apply(root)

    def test_pre_apply_external_drift_creates_no_intent_or_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(runner, 'load_manifest', return_value={'plan': {}}), \
                 patch.object(runner, 'evidence', return_value={}), \
                 patch.object(runner, 'reconcile', return_value={'status': 'foreign_or_unknown', 'outside_unchanged': False, 'native_same': True}), \
                 patch.object(runner, 'psql') as executor:
                with self.assertRaises(ValueError):
                    runner.apply(root)
                self.assertFalse((root / 'state.json').exists())
                executor.assert_not_called()

    def test_balance_evidence_rejects_nonfinite_and_boolean_funded_values(self):
        for value in (float('nan'), float('inf'), True, False):
            names = ('Code Plan', '寒鹤', 'Paisio', 'nodyhub', 'maolao API')
            data = {'updated_at': time.time(), 'updated_at_iso': 'test', 'providers': [
                {'name': name, 'status': 'complete', 'balance_usd': 0.01 if name == 'maolao API' else 10}
                for name in names]}
            data['providers'][0]['balance_usd'] = value
            with patch.object(Path, 'read_bytes', return_value=json.dumps(data).encode()), self.assertRaises(ValueError):
                runner.evidence()

    def test_balance_evidence_rejects_boolean_or_nonfinite_depleted_values(self):
        for value in (False, True, float('nan'), float('inf')):
            names = ('Code Plan', '寒鹤', 'Paisio', 'nodyhub', 'maolao API')
            data = {'updated_at': time.time(), 'updated_at_iso': 'test', 'providers': [
                {'name': name, 'status': 'complete', 'balance_usd': value if name == 'maolao API' else 10}
                for name in names]}
            with patch.object(Path, 'read_bytes', return_value=json.dumps(data).encode()), self.assertRaises(ValueError):
                runner.evidence()


if __name__ == "__main__":
    unittest.main()
