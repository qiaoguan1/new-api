import pathlib
import sys
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from daily_reconciliation import build_reconciliation  # noqa: E402


DAY = "2026-07-22"


def matcher(channel, upstream):
    return channel.get("slug") == upstream.get("slug")


def amount(quota):
    return float(quota or 0) / 500_000


class DailyReconciliationTests(unittest.TestCase):
    def test_complete_zero_is_distinct_from_incomplete_null(self):
        result = build_reconciliation(
            [{"slug": "zero", "name": "Zero"}, {"slug": "captcha", "name": "Captcha"}],
            [
                {"id": 1, "slug": "zero", "status": 1},
                {"id": 2, "slug": "captcha", "status": 1},
            ],
            {"channels": []},
            {
                "days": {
                    DAY: {
                        "zero": {
                            "collection_status": "complete",
                            "actual_log_complete": True,
                            "day_log_cost_cny": 0,
                            "day_log_rows": 0,
                        },
                        "captcha": {
                            "collection_status": "incomplete",
                            "actual_log_complete": False,
                            "day_log_cost_cny": None,
                            "collection_error": "captcha required",
                        },
                    }
                }
            },
            DAY,
            [
                {"channel_id": 1, "calls": 2, "success_calls": 2, "error_calls": 0, "quota": 500000},
                {"channel_id": 2, "calls": 1, "success_calls": 1, "error_calls": 0, "quota": 250000},
            ],
            {"zero", "captcha"},
            matcher,
            amount,
        )
        rows = {row["slug"]: row for row in result["rows"]}

        self.assertEqual(rows["zero"]["upstream_actual_cost_cny"], 0.0)
        self.assertEqual(rows["zero"]["difference_cny"], 1.0)
        self.assertIsNone(rows["captcha"]["upstream_actual_cost_cny"])
        self.assertIsNone(rows["captcha"]["difference_cny"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["totals"]["difference_cny"])

    def test_union_includes_credentials_and_credentialless_config(self):
        result = build_reconciliation(
            [{"slug": "configured"}],
            [],
            {"channels": [{"upstream_slug": "audited"}]},
            {"days": {DAY: {"ledger-only": {"collection_status": "incomplete"}}}},
            DAY,
            [],
            {"credential-only"},
            matcher,
            amount,
        )

        self.assertEqual(
            {row["slug"] for row in result["rows"]},
            {"configured", "audited", "ledger-only", "credential-only"},
        )
        configured = next(row for row in result["rows"] if row["slug"] == "configured")
        self.assertEqual(configured["collection_status"], "no_credentials")

    def test_unassigned_local_logs_are_disclosed_in_totals(self):
        result = build_reconciliation(
            [{"slug": "known"}],
            [{"id": 1, "slug": "known", "status": 1}],
            {"channels": []},
            {
                "days": {
                    DAY: {
                        "known": {
                            "collection_status": "complete",
                            "actual_log_complete": True,
                            "day_log_cost_cny": 0,
                            "day_log_rows": 0,
                        }
                    }
                }
            },
            DAY,
            [
                {"channel_id": 1, "calls": 2, "success_calls": 2, "error_calls": 0, "quota": 0},
                {"channel_id": 0, "calls": 4, "success_calls": 0, "error_calls": 4, "quota": 0},
            ],
            {"known"},
            matcher,
            amount,
        )

        self.assertEqual(result["totals"]["local_calls"], 6)
        self.assertEqual(result["totals"]["mapped_local_calls"], 2)
        self.assertEqual(result["totals"]["unassigned_local_calls"], 4)
        self.assertEqual(result["totals"]["unassigned_local_billed_cny"], 0.0)


if __name__ == "__main__":
    unittest.main()
