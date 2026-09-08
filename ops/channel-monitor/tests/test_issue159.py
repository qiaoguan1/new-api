import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
PRODUCTION_MODULES = pathlib.Path("/opt/ai-api-stack/channel-monitor")
sys.path.insert(0, str(PRODUCTION_MODULES))
sys.path.insert(0, str(PRODUCTION_MODULES / "scripts"))


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status_code = 409

    def json(self):
        return {"success": False, "code": "AUTH_SESSION_LIMIT", "message": "Conflict"}


class FakeSession:
    def post(self, *args, **kwargs):
        return FakeResponse()


class Issue159Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = load_module("candidate_scan", "scan-upstream-daily.py")
        cls.catalog = load_module("candidate_catalog", "upstream_video_catalog.py")
        cls.balance = load_module("candidate_balance", "fetch-upstream-balance.py")
        cls.patrol = load_module("candidate_patrol", "patrol_repair.py")
        cls.policy = json.loads((ROOT / "video-model-policy.json").read_text(encoding="utf-8"))

    def test_session_limit_code_is_preserved_in_login_error(self):
        with self.assertRaisesRegex(RuntimeError, "AUTH_SESSION_LIMIT"):
            self.balance.standard_login(FakeSession(), "https://www.0809.one", "user", "password")

    def test_topaz_uses_static_pricing_status_without_generic_pricing_request(self):
        original = self.scan.fetch_pricing
        self.scan.fetch_pricing = lambda *_: self.fail("Topaz must not call /api/pricing")
        try:
            result = self.scan.scan_pricing(
                {"type": 58, "models": "prob-4,iris-3", "model_mapping": ""},
                "",
            )
        finally:
            self.scan.fetch_pricing = original
        self.assertEqual("static", result["status"])
        self.assertEqual("configured_static_price", result["source"])

    def test_actual_cost_ledger_avoids_missing_pricing_endpoint(self):
        original = self.scan.fetch_pricing
        self.scan.fetch_pricing = lambda *_: self.fail("Actual-cost channel must not call /api/pricing")
        try:
            result = self.scan.scan_pricing(
                {"type": 1, "models": "gpt-5.6-sol", "model_mapping": ""},
                "",
                {
                    "collection_status": "complete",
                    "actual_log_complete": True,
                    "per_model_real_cost": {
                        "gpt-5.6-sol": {
                            "kind": "text",
                            "input_cost_cny_per_m": 1.0,
                            "output_cost_cny_per_m": 6.0,
                        }
                    },
                },
            )
        finally:
            self.scan.fetch_pricing = original
        self.assertEqual("actual", result["status"])
        self.assertEqual("actual_deduction_log", result["source"])
        self.assertTrue(result["models"]["gpt-5.6-sol"]["available"])
        self.assertEqual({}, result["unavailable_models"])

    def test_unreconciled_account_cost_does_not_create_margin_signal(self):
        cost, source, confidence = self.scan.actual_daily_cost_source(
            {
                "calls": 9,
                "success_calls": 8,
                "prompt_tokens": 215188,
                "completion_tokens": 3925,
                "local_charge_quota": 89525,
            },
            {},
            89525 / 500000,
            ledger_entry={
                "per_model_cost_usd": {"gpt-5.6-sol": 0.206561},
                "per_model_real_cost": {
                    "gpt-5.6-sol": {
                        "kind": "text",
                        "calls": 6,
                        "input_tokens": 180935,
                        "output_tokens": 4271,
                    }
                },
            },
            channel_models=["gpt-5.6-sol"],
        )
        self.assertEqual(0.206561, cost)
        self.assertEqual("upstream_log_unreconciled", source)
        self.assertEqual(0.0, confidence)

    def test_quota_type_fixed_catalog_is_trusted_video_cost(self):
        ledger = {
            "days": {
                "2026-09-07": {
                    "paisio": {
                        "collection_status": "complete",
                        "actual_log_complete": True,
                        "last_attempt_status": "complete",
                        "group": "default",
                        "rate": 1.0,
                        "pricing_metadata": {
                            "status": "complete",
                            "models": [
                                {
                                    "model_name": "sd3-720p",
                                    "model_price": 0.56,
                                    "quota_type": 1,
                                }
                            ],
                        },
                    }
                }
            }
        }
        costs = self.catalog.build_trusted_price_evidence(
            ledger,
            self.policy,
            target_day="2026-09-07",
        )
        row = next(item for item in costs if item["raw_model"] == "sd3-720p")
        self.assertEqual("call", row["billing_unit"])
        self.assertEqual(0.56, row["unit_cost_cny"])

    def test_boolean_quota_type_is_not_treated_as_fixed_price(self):
        ledger = {
            "days": {
                "2026-09-07": {
                    "paisio": {
                        "collection_status": "complete",
                        "actual_log_complete": True,
                        "last_attempt_status": "complete",
                        "pricing_metadata": {
                            "status": "complete",
                            "models": [{"model_name": "sd3-720p", "model_price": 0.56, "quota_type": True}],
                        },
                    }
                }
            }
        }
        costs = self.catalog.build_trusted_price_evidence(ledger, self.policy, target_day="2026-09-07")
        self.assertFalse(any(item["raw_model"] == "sd3-720p" for item in costs))

    def test_topaz_is_excluded_from_seedance_catalog_collection(self):
        self.assertIsNone(
            self.catalog.source_for_channel(
                {"id": 43, "type": 58, "name": "Topaz", "base_url": "https://api.topazlabs.com"},
                [],
            )
        )

    def test_sd3_and_sd4_names_follow_variant_rule(self):
        normalize = __import__("video_catalog_policy").normalize_model_name
        expected = {
            "sd3-720p": ("seedance-2.0", "720p"),
            "sd3-fast-480p": ("seedance-2.0-fast", "480p"),
            "sd4-pro8-720p": ("seedance-2.0", "720p"),
            "sd4-fast2-720p": ("seedance-2.0-fast", "720p"),
        }
        for raw_model, pair in expected.items():
            decision = normalize("paisio", raw_model, self.policy)
            self.assertEqual("matched", decision["status"], raw_model)
            self.assertEqual(pair, (decision["stable_model"], decision["resolution"]))

    def test_official_price_snapshot_is_current_and_unchanged(self):
        pricing = json.loads((ROOT / "official-video-pricing.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(pricing["valid_until"], "2026-10-08T23:59:59+08:00")
        self.assertEqual(46, pricing["models"]["seedance-2.0"]["cny_per_m_tokens_by_resolution"]["720p"]["no_video_input"])
        self.assertEqual(37, pricing["models"]["seedance-2.0-fast"]["cny_per_m_tokens_by_resolution"]["720p"]["no_video_input"])
        self.assertEqual(23, pricing["models"]["seedance-2.0-mini"]["cny_per_m_tokens_by_resolution"]["720p"]["no_video_input"])

    def test_patrol_detects_unsafe_root_like_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory).chmod(0o700)
            checks = self.patrol.PatrolChecks(self.patrol.CommandRunner())
            result = checks.evaluate(
                {"id": "filesystem.root_mode", "kind": "path_mode", "path": directory, "expected_mode": "755", "severity": "critical"},
                0,
            )
        self.assertEqual("failed", result.status)
        self.assertEqual("path_mode_mismatch", result.code)
        self.assertEqual("700", result.evidence["mode"])

    def test_patrol_policy_monitors_dns_service(self):
        policy = json.loads((ROOT / "patrol-repair-policy.json").read_text(encoding="utf-8"))
        validated = self.patrol.validate_policy(policy)
        resolved = next(item for item in validated["checks"] if item["id"] == "systemd.resolved")
        self.assertEqual("restart.resolved", resolved["repair_action"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
