import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from monitor_health_policy import (  # noqa: E402
    classify_health,
    is_alert,
    is_warning,
    summarize_enabled_channels,
)


NOW = 2_000_000_000


class MonitorHealthPolicyTests(unittest.TestCase):
    def test_disabled_history_is_excluded_and_inactive_is_not_alert(self):
        summary = summarize_enabled_channels(
            [{"status": 2, "calls_24h": 100, "errors_24h": 100, "balance": 0}], NOW
        )
        self.assertEqual(summary["calls_24h"], 0)
        health = classify_health(summary)
        self.assertEqual(health, "inactive")
        self.assertFalse(is_alert(health))
        self.assertFalse(is_warning(health))

    def test_only_enabled_channels_contribute_to_metrics_and_error_text(self):
        summary = summarize_enabled_channels(
            [
                {
                    "status": 1,
                    "calls_24h": 20,
                    "success_24h": 19,
                    "errors_24h": 1,
                    "quota_24h": 50,
                    "last_error": "active error",
                    "last_error_at": NOW - 10,
                },
                {
                    "status": 2,
                    "calls_24h": 500,
                    "success_24h": 0,
                    "errors_24h": 500,
                    "quota_24h": 900,
                    "last_error": "retired error",
                    "last_error_at": NOW,
                },
            ],
            NOW,
        )
        self.assertEqual(summary["calls_24h"], 20)
        self.assertEqual(summary["errors_24h"], 1)
        self.assertEqual(summary["quota_24h"], 50)
        self.assertEqual(summary["last_error"], "active error")
        self.assertEqual(classify_health(summary), "ok")

    def test_error_requires_minimum_volume_count_and_rate(self):
        noisy_small_sample = {
            "enabled_channels": 1,
            "calls_24h": 4,
            "errors_24h": 2,
            "error_rate_24h": 0.5,
            "test_stale": True,
        }
        self.assertEqual(classify_health(noisy_small_sample), "ok")
        incident = {**noisy_small_sample, "calls_24h": 20, "errors_24h": 5, "error_rate_24h": 0.25}
        self.assertEqual(classify_health(incident), "error")
        self.assertTrue(is_alert(classify_health(incident)))

    def test_unknown_zero_balance_is_not_low_balance(self):
        summary = summarize_enabled_channels(
            [{"status": 1, "balance": 0, "balance_updated_time": 0, "calls_24h": 1}], NOW
        )
        self.assertIsNone(summary["db_balance"])
        self.assertEqual(classify_health(summary), "ok")

    def test_recent_zero_balance_is_low_balance(self):
        summary = summarize_enabled_channels(
            [
                {
                    "status": 1,
                    "balance": 0,
                    "balance_updated_time": NOW - 60,
                    "calls_24h": 1,
                }
            ],
            NOW,
        )
        self.assertEqual(summary["db_balance"], 0)
        self.assertEqual(classify_health(summary), "low_balance")

    def test_idle_upstream_with_stale_test_is_warning(self):
        summary = summarize_enabled_channels(
            [{"status": 1, "test_time": NOW - 7201}], NOW
        )
        self.assertTrue(summary["test_stale"])
        self.assertEqual(summary["health_source"], "none")
        self.assertEqual(classify_health(summary), "stale")
        self.assertTrue(is_warning(classify_health(summary)))

    def test_stale_slow_measurement_does_not_raise_slow_alert(self):
        summary = summarize_enabled_channels(
            [{"status": 1, "test_time": NOW - 7201, "response_time": 9000, "calls_24h": 1}],
            NOW,
        )
        self.assertEqual(classify_health(summary), "ok")

    def test_only_fresh_test_latency_is_averaged_and_latest_error_wins(self):
        summary = summarize_enabled_channels(
            [
                {
                    "status": 1,
                    "test_time": NOW - 60,
                    "response_time": 100,
                    "last_error": "older",
                    "last_error_at": NOW - 30,
                    "calls_24h": 1,
                },
                {
                    "status": 1,
                    "test_time": NOW - 7201,
                    "response_time": 20_000,
                    "last_error": "newer",
                    "last_error_at": NOW - 10,
                },
            ],
            NOW,
        )
        self.assertEqual(summary["avg_response_ms"], 100)
        self.assertEqual(summary["last_error"], "newer")


if __name__ == "__main__":
    unittest.main()
