import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "daily-ops-digest.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("daily_ops_digest", SCRIPT_PATH)
digest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(digest)


DAY = "2026-08-11"


def source(*, status="complete", calls=0, cost=0):
    return {
        "collection_status": status,
        "actual_log_complete": status == "complete",
        "day_log_rows": calls if status == "complete" else None,
        "day_log_cost_cny": cost if status == "complete" else None,
    }


class DigestBuilderTests(unittest.TestCase):
    def fixture(self):
        upstreams = [
            {"slug": "paisio", "name": "Paisio"},
            {"slug": "broken", "name": "Broken"},
        ]
        ledger = {
            "days": {
                "2026-08-01": {"paisio": source(calls=2, cost=1.25)},
                DAY: {
                    "paisio": source(calls=5, cost=3.5),
                    "broken": source(status="incomplete"),
                },
            }
        }
        audit = {
            "date": DAY,
            "channels": [
                {"upstream_slug": "paisio", "scan_status": "ok"},
                {"upstream_slug": "broken", "scan_status": "error"},
            ],
            "alerts": [{"type": "availability_failed"}],
        }
        pricing = {
            "runs": [{
                "date": DAY,
                "status": "complete",
                "generated_at": 100,
                "decisions": [
                    {"model": "text", "action": "apply", "reason": "ok"},
                    {"model": "image", "action": "skip", "reason": "upstream_collection_incomplete"},
                    {"model": "video", "action": "skip", "reason": "video_official_pricing_only"},
                ],
            }]
        }
        live = {
            "updated_at_iso": "2026-08-12T09:02:00+08:00",
            "providers": {
                "paisio": {"status": "complete", "balance_usd": 85.79},
                "broken": {"status": "unknown", "balance_usd": None},
            },
        }
        return upstreams, ledger, audit, pricing, live

    def test_digest_contains_each_channel_daily_and_month_to_date_usage(self):
        report = digest.build_digest(*self.fixture(), DAY, generated_at=200)

        channels = {row["slug"]: row for row in report["channels"]}
        self.assertEqual(channels["paisio"]["daily_calls"], 5)
        self.assertEqual(channels["paisio"]["daily_cost_cny"], 3.5)
        self.assertEqual(channels["paisio"]["month_calls"], 7)
        self.assertEqual(channels["paisio"]["month_cost_cny"], 4.75)
        self.assertEqual(channels["broken"]["collection_status"], "incomplete")
        self.assertEqual(channels["broken"]["balance_status"], "unknown")
        self.assertIsNone(channels["broken"]["balance"])
        self.assertEqual(report["pricing"]["applied"], 1)
        self.assertEqual(report["pricing"]["blocked"], 1)
        self.assertEqual(report["pricing"]["protected_video"], 1)
        self.assertEqual(report["audit"]["ok_channels"], 1)

    def test_missing_daily_artifacts_fail_closed_instead_of_sending_partial_report(self):
        upstreams, ledger, audit, pricing, live = self.fixture()
        del ledger["days"][DAY]
        with self.assertRaisesRegex(digest.DigestError, "ledger_day_missing"):
            digest.build_digest(upstreams, ledger, audit, pricing, live, DAY, generated_at=200)

    def test_delivery_state_is_written_only_after_success_and_deduplicates(self):
        upstreams, ledger, audit, pricing, live = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = {
                "upstreams": root / "upstreams.json",
                "ledger": root / "ledger.json",
                "audit": root / "audit.json",
                "pricing": root / "pricing.json",
                "live": root / "live.json",
                "recharges": root / "recharges.json",
                "state": root / "state.json",
            }
            for key, value in (
                ("upstreams", upstreams), ("ledger", ledger), ("audit", audit),
                ("pricing", pricing), ("live", live),
                (
                    "recharges",
                    {
                        "source": "authenticated_upstream_recharge_records",
                        "generated_at": int(digest.time.time()),
                        "complete": 0,
                        "unavailable": 0,
                        "providers": {},
                    },
                ),
            ):
                paths[key].write_text(json.dumps(value), encoding="utf-8")
            argv = [
                "--date", DAY,
                "--upstreams", str(paths["upstreams"]),
                "--ledger", str(paths["ledger"]),
                "--audit", str(paths["audit"]),
                "--pricing-log", str(paths["pricing"]),
                "--live-balance", str(paths["live"]),
                "--recharges", str(paths["recharges"]),
                "--state", str(paths["state"]),
            ]
            with mock.patch.object(
                digest, "send_digest", side_effect=digest.DigestError("digest_delivery_failed")
            ):
                self.assertEqual(digest.main(argv, environ={}), 2)
            self.assertFalse(paths["state"].exists())

            with mock.patch.object(digest, "send_digest") as send:
                self.assertEqual(digest.main(argv, environ={}), 0)
                self.assertEqual(digest.main(argv, environ={}), 0)
            send.assert_called_once()
            state = json.loads(paths["state"].read_text(encoding="utf-8"))
            self.assertIn(DAY, state["delivered_dates"])

    def test_notification_body_contains_no_credentials_or_raw_errors(self):
        report = digest.build_digest(*self.fixture(), DAY, generated_at=200)
        payload = digest.notification_payload(report)
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("website_url", serialized.lower())
        self.assertLess(len(serialized.encode("utf-8")), 64 * 1024)

    def test_digest_includes_only_sanitized_recharge_aggregates(self):
        recharges = {
            "source": "authenticated_upstream_recharge_records",
            "generated_at": 199,
            "complete": 1,
            "unavailable": 1,
            "providers": {
                "paisio": {
                    "name": "Paisio",
                    "status": "complete",
                    "successful_records": 2,
                    "credited_amount": 10.2,
                    "credited_unit": "upstream_account_money",
                    "paid_amounts": {"CNY": 10},
                    "trade_no": "must-not-leak",
                },
                "broken": {
                    "name": "Broken",
                    "status": "unavailable",
                    "unavailable_reason": "contains private upstream details",
                },
            },
        }

        report = digest.build_digest(
            *self.fixture(), DAY, generated_at=200, recharges=recharges
        )
        payload = digest.notification_payload(report)

        self.assertEqual(payload["recharges"]["paid_cny_total"], 10.0)
        self.assertEqual(payload["recharges"]["complete"], 1)
        self.assertNotIn("trade_no", json.dumps(payload))
        self.assertNotIn("private upstream details", json.dumps(payload))

    def test_stale_recharge_summary_fails_closed(self):
        recharges = {
            "source": "authenticated_upstream_recharge_records",
            "generated_at": 1,
            "complete": 0,
            "unavailable": 0,
            "providers": {},
        }
        with self.assertRaisesRegex(digest.DigestError, "recharge_summary_stale"):
            digest.build_digest(
                *self.fixture(), DAY, generated_at=200_000, recharges=recharges
            )


if __name__ == "__main__":
    unittest.main()
