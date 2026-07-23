import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch-upstream-balance.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("fetch_upstream_balance", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class UsageV1AggregationTests(unittest.TestCase):
    def test_authenticated_pricing_metadata_is_sanitized(self):
        session = mock.Mock()
        pricing_response = mock.Mock(status_code=200)
        pricing_response.json.return_value = {
            "success": True,
            "pricing_version": "v1",
            "group_ratio": {"default": 1},
            "data": [{
                "model_name": "video-pro-720p",
                "model_ratio": 2.5,
                "completion_ratio": 1,
                "enable_groups": ["default"],
                "billing_mode": "ratio",
                "billing_expr": "",
                "secret_internal_field": "must-not-persist",
            }],
        }
        account_models_response = mock.Mock(status_code=200)
        account_models_response.json.return_value = {
            "success": True,
            "data": ["video-pro-720p", {"id": "gpt-5.6-sol"}],
        }
        session.get.side_effect = [pricing_response, account_models_response]

        metadata = collector.standard_pricing_metadata(session, "https://example.test")

        self.assertEqual(metadata["status"], "complete")
        self.assertEqual(metadata["models"][0]["model_name"], "video-pro-720p")
        self.assertEqual(metadata["account_models"], ["gpt-5.6-sol", "video-pro-720p"])
        self.assertNotIn("secret_internal_field", metadata["models"][0])

    def test_account_model_metadata_failure_is_fail_closed(self):
        pricing_response = mock.Mock(status_code=200)
        pricing_response.json.return_value = {"success": True, "data": []}
        models_response = mock.Mock(status_code=401)
        models_response.json.return_value = {"success": False, "message": "unauthorized"}
        session = mock.Mock()
        session.get.side_effect = [pricing_response, models_response]

        with self.assertRaisesRegex(RuntimeError, "account models failed"):
            collector.standard_pricing_metadata(session, "https://example.test")

    def test_complete_entry_preserves_pricing_metadata(self):
        metadata = {"status": "complete", "models": [{"model_name": "gpt-5.6-sol"}]}
        entry = collector.complete_entry(
            "newapi_classic", 1.0, "default", 5.0, 1.0, 0.0, 0, {}, {}, None,
            pricing_metadata=metadata,
        )

        self.assertEqual(entry["pricing_metadata"], metadata)

    def test_metadata_failure_preserves_complete_actual_cost_and_redacts_secret(self):
        with (
            mock.patch.object(collector.requests, "Session"),
            mock.patch.object(collector, "standard_login", return_value="default"),
            mock.patch.object(
                collector,
                "standard_self",
                return_value={"quota": 5_000_000, "used_quota": 1_000_000},
            ),
            mock.patch.object(
                collector,
                "standard_logs",
                return_value=(1, 500_000, {"gpt-5.6-sol": 500_000}, {}),
            ),
            mock.patch.object(
                collector,
                "standard_pricing_metadata",
                side_effect=RuntimeError("upstream echoed super-secret"),
            ),
        ):
            entry = collector.collect_one(
                "paisio",
                {
                    "username": "account-name",
                    "password": "super-secret",
                    "website_url": "https://example.test",
                    "rate": 1,
                },
                "",
                {"days": {}},
                "2026-07-22",
            )

        self.assertEqual(entry["collection_status"], "complete")
        self.assertEqual(entry["day_log_cost_cny"], 1.0)
        self.assertEqual(entry["pricing_metadata"]["status"], "unavailable")
        self.assertNotIn("super-secret", entry["pricing_metadata"]["error"])

    def test_uses_account_total_cost_not_internal_actual_cost(self):
        total, per_model, real = collector.aggregate_v1_rows(
            [
                {
                    "model": "gpt-test",
                    "total_cost": 0.75,
                    "actual_cost": 0.10,
                    "input_cost": 0.50,
                    "output_cost": 0.25,
                    "input_tokens": 100_000,
                    "output_tokens": 10_000,
                }
            ],
            rate=1.0,
        )

        self.assertEqual(total, 0.75)
        self.assertEqual(per_model["gpt-test"], 0.75)
        self.assertEqual(real["gpt-test"]["input_cost_cny_per_m"], 5.0)
        self.assertEqual(real["gpt-test"]["output_cost_cny_per_m"], 25.0)

    def test_successful_empty_query_is_complete_zero(self):
        entry = collector.complete_entry(
            "usage_v1", 1.0, "", 5.0, None, 0.0, 0, {}, {}, None
        )

        self.assertEqual(entry["collection_status"], "complete")
        self.assertTrue(entry["actual_log_complete"])
        self.assertEqual(entry["day_log_cost_cny"], 0.0)
        self.assertEqual(entry["day_log_rows"], 0)

    def test_failure_is_null_and_preserves_no_fake_zero(self):
        entry = collector.failed_entry(None, "captcha required")

        self.assertEqual(entry["collection_status"], "incomplete")
        self.assertFalse(entry["actual_log_complete"])
        self.assertIsNone(entry["day_log_cost_cny"])
        self.assertIsNone(entry["day_log_rows"])

    def test_failed_retry_preserves_previous_complete_collection(self):
        prior = collector.complete_entry(
            "newapi_classic", 1.0, "", 10.0, 2.0, 1.25, 3, {}, {}, None
        )
        entry = collector.failed_entry(prior, "temporary timeout")

        self.assertEqual(entry["collection_status"], "complete")
        self.assertEqual(entry["day_log_cost_cny"], 1.25)
        self.assertEqual(entry["last_attempt_status"], "incomplete")

    def test_error_sanitizer_redacts_credentials(self):
        self.assertEqual(
            collector.clean_error("login alice@example.test secret", ("alice@example.test", "secret")),
            "login [redacted] [redacted]",
        )

    def test_collector_refuses_to_send_credentials_over_http(self):
        with self.assertRaisesRegex(RuntimeError, "non-HTTPS"):
            collector.collect_one(
                "unsafe",
                {"username": "user", "password": "secret", "website_url": "http://example.test"},
                "",
                {"days": {}},
                "2026-07-22",
            )


if __name__ == "__main__":
    unittest.main()
