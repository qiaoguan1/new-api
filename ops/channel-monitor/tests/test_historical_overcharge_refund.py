import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "historical-overcharge-refund.py"
)
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("historical_overcharge_refund", SCRIPT_PATH)
refund = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(refund)


def complete_source(**models):
    return {
        "collection_status": "complete",
        "actual_log_complete": True,
        "day_log_rows": sum(item.get("calls", 0) for item in models.values()),
        "per_model_real_cost": models,
    }


def text_cost(input_cost, output_cost, calls=1):
    return {
        "kind": "text",
        "calls": calls,
        "input_cost_cny_per_m": input_cost,
        "output_cost_cny_per_m": output_cost,
    }


def fixed_cost(cost, calls=1):
    return {"kind": "fixed", "calls": calls, "cost_cny_per_call": cost}


def usage_log(**overrides):
    row = {
        "id": 101,
        "user_id": 7,
        "username": "customer",
        "token_id": 9,
        "created_at": 1784736000,
        "beijing_date": "2026-07-22",
        "model_name": "gpt-5.5",
        "quota": 8_000_000,
        "prompt_tokens": 1_000_000,
        "completion_tokens": 100_000,
        "request_id": "request-101",
        "other": {
            "model_price": -1,
            "model_ratio": 37.5,
            "completion_ratio": 8,
            "group_ratio": 0.15,
            "cache_tokens": 200_000,
            "cache_ratio": 0.1,
        },
    }
    row.update(overrides)
    return row


class EvidenceTests(unittest.TestCase):
    def test_selects_highest_input_and_output_cost_across_complete_sources(self):
        ledger = {
            "days": {
                "2026-07-22": {
                    "cheap": complete_source(**{"gpt-5.5": text_cost(1.0, 9.0)}),
                    "input-high": complete_source(
                        **{"gpt-5.5": text_cost(5.0, 8.0)}
                    ),
                    "packapi": complete_source(
                        **{"gpt-5.5": text_cost(99.0, 99.0)}
                    ),
                }
            }
        }

        index = refund.build_evidence_index(ledger)
        evidence = index[("2026-07-22", "gpt-5.5")]

        self.assertEqual(evidence["kind"], "text")
        self.assertEqual(evidence["input_cost_cny_per_m"], 5.0)
        self.assertEqual(evidence["input_source"], "input-high")
        self.assertEqual(evidence["output_cost_cny_per_m"], 9.0)
        self.assertEqual(evidence["output_source"], "cheap")
        self.assertNotIn("packapi", evidence["observed_sources"])

    def test_accepts_positive_legacy_actual_samples_but_labels_them(self):
        ledger = {
            "days": {
                "2026-07-21": {
                    "legacy": {
                        "day_log_rows": 3,
                        "per_model_real_cost": {
                            "gpt-5.6-sol": text_cost(7.5, 60.0, calls=3)
                        },
                    }
                }
            }
        }

        evidence = refund.build_evidence_index(ledger)[
            ("2026-07-21", "gpt-5.6-sol")
        ]

        self.assertEqual(evidence["quality"], "legacy_actual_sample")

    def test_rejects_incomplete_or_zero_call_costs(self):
        ledger = {
            "days": {
                "2026-07-22": {
                    "failed": {
                        "collection_status": "incomplete",
                        "actual_log_complete": False,
                        "day_log_rows": 2,
                        "per_model_real_cost": {"x": fixed_cost(2.0, calls=2)},
                    },
                    "zero": complete_source(**{"y": fixed_cost(2.0, calls=0)}),
                }
            }
        }

        index = refund.build_evidence_index(ledger)

        self.assertNotIn(("2026-07-22", "x"), index)
        self.assertNotIn(("2026-07-22", "y"), index)

    def test_one_incomplete_expected_source_blocks_the_entire_modern_day(self):
        ledger = {
            "days": {
                "2026-07-22": {
                    "cheap-complete": complete_source(
                        **{"gpt-5.5": text_cost(1.0, 8.0)}
                    ),
                    "expensive-rate-limited": {
                        "collection_status": "incomplete",
                        "actual_log_complete": False,
                        "collection_error": "HTTP 429",
                        "per_model_real_cost": {},
                    },
                }
            }
        }

        self.assertEqual(refund.build_evidence_index(ledger), {})


class CalculationTests(unittest.TestCase):
    def test_text_target_preserves_cache_ratio_and_rounds_like_newapi(self):
        evidence = {
            "kind": "text",
            "input_cost_cny_per_m": 5.0,
            "output_cost_cny_per_m": 40.0,
        }

        result = refund.calculate_refund(usage_log(), evidence)

        # Actual cost: ((800k + 200k*0.1)*5 + 100k*40) / 1m = CNY 8.1.
        # Customer policy: CNY 8.1 * 1.5 * 500,000 quota/CNY.
        self.assertEqual(result["policy_quota"], 6_075_000)
        self.assertEqual(result["refund_quota"], 1_925_000)

    def test_cache_creation_semantics_are_preserved(self):
        row = usage_log(
            prompt_tokens=1_000,
            completion_tokens=0,
            quota=10_000,
            other={
                "model_price": -1,
                "cache_tokens": 200,
                "cache_ratio": 0.1,
                "cache_creation_tokens": 300,
                "cache_creation_ratio": 1.25,
            },
        )
        evidence = {
            "kind": "text",
            "input_cost_cny_per_m": 10.0,
            "output_cost_cny_per_m": 20.0,
        }

        result = refund.calculate_refund(row, evidence)

        # Weighted input tokens: 500 + 200*.1 + 300*1.25 = 895.
        self.assertEqual(result["policy_quota"], 6_713)

    def test_fixed_task_uses_actual_cost_per_call(self):
        row = usage_log(
            model_name="video-fast-480p",
            quota=8_437_500,
            prompt_tokens=1_500_000,
            completion_tokens=0,
            other={"model_price": -1, "is_task": True, "group_ratio": 0.15},
        )

        result = refund.calculate_refund(
            row, {"kind": "fixed", "cost_cny_per_call": 1.05}
        )

        self.assertEqual(result["policy_quota"], 787_500)
        self.assertEqual(result["refund_quota"], 7_650_000)

    def test_does_not_refund_when_original_is_at_or_below_policy(self):
        row = usage_log(quota=6_075_000)
        evidence = {
            "kind": "text",
            "input_cost_cny_per_m": 5.0,
            "output_cost_cny_per_m": 40.0,
        }

        result = refund.calculate_refund(row, evidence)

        self.assertEqual(result["refund_quota"], 0)

    def test_rejects_text_log_with_impossible_cache_tokens(self):
        row = usage_log(
            other={
                "model_price": -1,
                "cache_tokens": 1_000_001,
                "cache_ratio": 0.1,
            }
        )

        with self.assertRaises(refund.RefundError):
            refund.calculate_refund(
                row,
                {
                    "kind": "text",
                    "input_cost_cny_per_m": 5.0,
                    "output_cost_cny_per_m": 40.0,
                },
            )


class PlanTests(unittest.TestCase):
    def test_plan_is_per_request_per_user_and_skips_existing_refunds(self):
        logs = [usage_log(id=101), usage_log(id=102, user_id=8, username="second")]
        index = {
            ("2026-07-22", "gpt-5.5"): {
                "kind": "text",
                "quality": "complete_actual_log",
                "input_cost_cny_per_m": 5.0,
                "output_cost_cny_per_m": 40.0,
            }
        }

        plan = refund.build_refund_plan(logs, index, already_refunded={102})

        self.assertEqual([item["source_log_id"] for item in plan["refunds"]], [101])
        self.assertEqual(plan["totals"]["refund_requests"], 1)
        self.assertEqual(plan["totals"]["affected_users"], 1)
        self.assertEqual(plan["totals"]["already_refunded"], 1)
        self.assertEqual(plan["users"][0]["user_id"], 7)

    def test_missing_evidence_is_reported_without_refund(self):
        plan = refund.build_refund_plan([usage_log(model_name="unknown")], {})

        self.assertEqual(plan["refunds"], [])
        self.assertEqual(plan["totals"]["missing_evidence"], 1)
        self.assertEqual(plan["skipped"][0]["reason"], "missing_actual_cost")

    def test_plan_checksum_changes_when_any_refund_amount_changes(self):
        logs = [usage_log()]
        index = {
            ("2026-07-22", "gpt-5.5"): {
                "kind": "text",
                "quality": "complete_actual_log",
                "input_cost_cny_per_m": 5.0,
                "output_cost_cny_per_m": 40.0,
            }
        }
        first = refund.build_refund_plan(logs, index)
        logs[0]["quota"] += 1
        second = refund.build_refund_plan(logs, index)

        self.assertNotEqual(first["plan_sha256"], second["plan_sha256"])

    def test_transaction_validation_uses_rollback(self):
        plan = refund.build_refund_plan(
            [usage_log()],
            {
                ("2026-07-22", "gpt-5.5"): {
                    "kind": "text",
                    "quality": "complete_actual_log",
                    "input_cost_cny_per_m": 5.0,
                    "output_cost_cny_per_m": 40.0,
                }
            },
        )

        sql = refund._execution_sql(plan, commit=False)

        self.assertTrue(sql.rstrip().endswith("ROLLBACK;"))
        self.assertNotIn("\nCOMMIT;", sql)
        self.assertIn("LEAST(t.used_quota,a.refund)", sql)
        self.assertNotIn("token used quota would underflow", sql)
        self.assertIn("u.used_quota>=a.refund", sql)

    def test_plan_validation_rejects_tampered_totals(self):
        plan = refund.build_refund_plan(
            [usage_log()],
            {
                ("2026-07-22", "gpt-5.5"): {
                    "kind": "text",
                    "quality": "complete_actual_log",
                    "input_cost_cny_per_m": 5.0,
                    "output_cost_cny_per_m": 40.0,
                }
            },
        )
        plan["totals"]["refund_quota"] += 1

        with self.assertRaises(refund.RefundError):
            refund._validate_plan(plan, plan["plan_sha256"])

    @mock.patch.object(refund.subprocess, "run")
    def test_live_execution_refuses_when_public_ingress_is_running(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="postgres\nnew-api\nnginx\n")

        with self.assertRaises(refund.RefundError):
            refund.assert_maintenance_window()


if __name__ == "__main__":
    unittest.main()
