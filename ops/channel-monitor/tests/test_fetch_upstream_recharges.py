import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "fetch-upstream-recharges.py"
)
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("fetch_upstream_recharges", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)


class Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class RechargeAggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert SPEC.loader is not None
        SPEC.loader.exec_module(collector)

    def test_only_successful_records_contribute_to_totals(self):
        summary = collector.summarize_recharges(
            [
                {
                    "status": "success",
                    "money": 50,
                    "paid_amount_cny": 48.5,
                    "create_time": 100,
                    "complete_time": 110,
                    "trade_no": "must-not-leak",
                },
                {"status": "pending", "money": 100, "paid_amount_cny": 100},
                {"status": "failed", "money": 200, "paid_amount_cny": 200},
            ]
        )

        self.assertEqual(summary["successful_records"], 1)
        self.assertEqual(summary["credited_amount"], 50.0)
        self.assertEqual(summary["paid_amounts"], {"CNY": 48.5})
        self.assertEqual(summary["first_record_at"], 100)
        self.assertEqual(summary["last_completed_at"], 110)
        self.assertNotIn("trade_no", repr(summary))

    def test_provider_currency_is_preserved_instead_of_summed_as_cny(self):
        summary = collector.summarize_recharges(
            [
                {
                    "status": "success",
                    "money": 70,
                    "provider_amount": 10,
                    "provider_currency": "USD",
                },
                {
                    "status": "success",
                    "money": 35,
                    "paid_amount": 5,
                    "currency": "USD",
                },
            ]
        )

        self.assertEqual(summary["credited_amount"], 105.0)
        self.assertEqual(summary["paid_amounts"], {"USD": 15.0})

    def test_placeholder_zero_paid_amount_does_not_hide_positive_order_amount(self):
        summary = collector.summarize_recharges(
            [
                {
                    "status": "success",
                    "money": 150,
                    "paid_amount": 0,
                    "expected_amount": 0,
                    "amount": 100,
                }
            ]
        )

        self.assertEqual(summary["credited_amount"], 150.0)
        self.assertEqual(summary["paid_amounts"], {"CNY": 100.0})

    def test_classic_pagination_requires_all_reported_records(self):
        session = Session(
            [
                Response(
                    {
                        "success": True,
                        "data": {
                            "items": [{"status": "success", "money": 1}],
                            "total": 2,
                        },
                    }
                ),
                Response(
                    {
                        "success": True,
                        "data": {
                            "items": [{"status": "success", "money": 2}],
                            "total": 2,
                        },
                    }
                ),
            ]
        )

        rows = collector.fetch_classic_recharges(
            session, "https://example.test", page_size=1, max_pages=2
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(session.calls[0][1]["params"]["p"], 1)
        self.assertEqual(session.calls[1][1]["params"]["p"], 2)

    def test_incomplete_pagination_fails_closed(self):
        session = Session(
            [
                Response(
                    {
                        "success": True,
                        "data": {
                            "items": [{"status": "success", "money": 1}],
                            "total": 3,
                        },
                    }
                )
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "pagination incomplete"):
            collector.fetch_classic_recharges(
                session, "https://example.test", page_size=1, max_pages=1
            )

    def test_toonflow_completed_orders_use_points_and_paid_cny(self):
        session = Session(
            [
                Response(
                    {
                        "code": 200,
                        "data": {
                            "data": [
                                {
                                    "status": 2,
                                    "points": 500,
                                    "amount": 50,
                                    "type": 1,
                                    "creationTime": 100,
                                    "paymentTime": 110,
                                    "orderNumber": "must-not-leak",
                                },
                                {"status": 3, "points": 900, "amount": 90},
                            ],
                            "total": 2,
                        },
                    }
                )
            ]
        )

        summary = collector.fetch_toonflow_recharges(
            session, "https://api.toonflow.net", page_size=100, max_pages=1
        )

        self.assertEqual(summary["successful_records"], 1)
        self.assertEqual(summary["credited_amount"], 500.0)
        self.assertEqual(summary["paid_amounts"], {"CNY": 50.0})
        self.assertEqual(summary["type_counts"], {"admin_recharge": 1})
        self.assertNotIn("orderNumber", repr(summary))

    def test_private_atomic_output_does_not_persist_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "summary.json"
            with mock.patch.object(collector.os, "chmod") as chmod:
                collector.write_private_json(
                    path,
                    {"providers": {"sample": {"successful_records": 1}}},
                )

            chmod.assert_called_once_with(pathlib.Path(str(path) + ".tmp"), 0o600)
            self.assertNotIn("trade_no", path.read_text(encoding="utf-8"))

    def test_toonflow_token_is_never_sent_to_an_unapproved_host(self):
        with self.assertRaisesRegex(RuntimeError, "unapproved Toonflow host"):
            collector.validate_toonflow_origin("https://attacker.example")

    def test_millisecond_provider_timestamps_are_normalized_to_seconds(self):
        self.assertEqual(collector._epoch(1_786_538_652_606), 1_786_538_652)


if __name__ == "__main__":
    unittest.main()
