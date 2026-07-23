import importlib.util
import pathlib
import unittest


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch-upstream-balance.py"
SPEC = importlib.util.spec_from_file_location("fetch_upstream_balance", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class UsageV1AggregationTests(unittest.TestCase):
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
