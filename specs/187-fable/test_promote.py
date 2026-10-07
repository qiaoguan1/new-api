"""Offline key promotion safety contracts; fake upstream responses only."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("promote_keys", Path(__file__).with_name("promote_keys.py"))
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


def writer(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(value, handle)


def secret():
    return {"token_id": 42, "name": "fable187-owned", "key": "sk-private-fixture", "origin": "https://rolldek.com",
            "api_origin": "https://rolldek.com", "group": "ccmax", "cny_per_credit": 1}


def token(production=False, **changes):
    return {"id": 42, "name": "fable187-owned", "key": "private-fixture", "status": 1, "group": "ccmax",
            "model_limits_enabled": True, "model_limits": promotion.MODEL, "allow_ips": promotion.IP,
            "cross_group_retry": False, "expired_time": -1 if production else 9999999999,
            "remain_quota": 50000, "unlimited_quota": production, **changes}


class PromotionTests(unittest.TestCase):
    def fixture(self, directory, records):
        root = Path(directory)
        probe, reader, session = Mock(), Mock(), Mock()
        reader.read.side_effect = lambda name: json.loads((root / name).read_text())
        probe.token_records.side_effect = [[copy.deepcopy(row)] for row in records]
        session.put.return_value = Mock(status_code=200)
        session.put.return_value.json.return_value = {"success": True}
        promoter = promotion.KeyPromoter(reader, probe, root, writer)
        return promoter, probe, reader, session

    def test_finite_promotion_submits_exactly_once_and_next_run_is_readback_only(self):
        with tempfile.TemporaryDirectory() as directory:
            promoter, probe, _, session = self.fixture(directory, [token(), token(True), token(True)])
            result = promoter.promote(session, "rolldek-ccmax", secret())
            promoter.promote(session, "rolldek-ccmax", secret())
            self.assertTrue(result["verified_production_key"])
            session.put.assert_called_once()
            probe.validate_probe_token.assert_called_once()
            body = session.put.call_args.kwargs["json"]
            self.assertEqual({key for key in body if body[key] != token()[key]}, {"expired_time", "unlimited_quota"})
            self.assertFalse(session.put.call_args.kwargs["allow_redirects"])
            proof = json.loads((Path(directory) / "rolldek-ccmax-production-key-verified.json").read_text())
            self.assertEqual(proof["key_sha256"], promotion.key_hash(secret()["key"]))
            self.assertNotIn(secret()["key"], json.dumps(proof))

    def test_already_promoted_key_does_not_put_or_create_started_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            promoter, _, _, session = self.fixture(directory, [token(True)])
            promoter.promote(session, "rolldek-ccmax", secret())
            session.put.assert_not_called()
            self.assertFalse((Path(directory) / "rolldek-ccmax-promotion-started.json").exists())

    def test_ambiguous_put_stays_fenced_and_no_retry_occurs_if_state_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            promoter, _, _, session = self.fixture(directory, [token(), token()])
            session.put.side_effect = TimeoutError("fixture timeout")
            with self.assertRaises(TimeoutError):
                promoter.promote(session, "rolldek-ccmax", secret())
            with self.assertRaisesRegex(RuntimeError, "uncertain promotion unresolved"):
                promoter.promote(session, "rolldek-ccmax", secret())
            session.put.assert_called_once()
            self.assertTrue((Path(directory) / "rolldek-ccmax-promotion-started.json").is_file())

    def test_confirmed_success_after_ambiguous_put_attests_without_repeating_write(self):
        with tempfile.TemporaryDirectory() as directory:
            promoter, _, _, session = self.fixture(directory, [token(), token(True)])
            session.put.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                promoter.promote(session, "rolldek-ccmax", secret())
            result = promoter.promote(session, "rolldek-ccmax", secret())
            self.assertTrue(result["verified_production_key"])
            session.put.assert_called_once()

    def test_live_rotated_key_paused_token_or_changed_scope_never_promotes(self):
        for changes in [{"key": "rotated-key"}, {"status": 2}, {"group": "other"}, {"allow_ips": ""},
                        {"cross_group_retry": True}, {"model_limits_enabled": False}, {"status": True}]:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                promoter, _, _, session = self.fixture(directory, [token(**changes)])
                with self.assertRaises(RuntimeError):
                    promoter.promote(session, "rolldek-ccmax", secret())
                session.put.assert_not_called()

    def test_masked_key_is_revealed_only_via_read_endpoint_and_hash_compared(self):
        with tempfile.TemporaryDirectory() as directory:
            promoter, _, _, session = self.fixture(directory, [token(True, key="sk-****")])
            session.post.return_value = Mock(status_code=200)
            session.post.return_value.json.return_value = {"success": True, "data": {"key": secret()["key"]}}
            promoter.promote(session, "rolldek-ccmax", secret())
            self.assertEqual(session.post.call_args.args[0], "https://rolldek.com/api/token/42/key")
            self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
            session.put.assert_not_called()

    def test_unapproved_origin_rejected_before_any_upstream_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            promoter, probe, _, session = self.fixture(directory, [])
            with self.assertRaises(RuntimeError):
                promoter.promote(session, "rolldek-ccmax", {**secret(), "origin": "https://other.example"})
            probe.token_records.assert_not_called()
            session.put.assert_not_called()

    def test_runtime_safety_gates_are_not_optimization_removable_asserts(self):
        tree = ast.parse(Path(promotion.__file__).read_text())
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))

    def test_existing_marker_for_different_identity_cannot_refresh_or_repeat_put(self):
        with tempfile.TemporaryDirectory() as directory:
            writer(Path(directory) / "rolldek-ccmax-promotion-started.json", {"source": "wrong"})
            promoter, _, _, session = self.fixture(directory, [token(True)])
            with self.assertRaisesRegex(RuntimeError, "marker identity changed"):
                promoter.promote(session, "rolldek-ccmax", secret())
            session.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
