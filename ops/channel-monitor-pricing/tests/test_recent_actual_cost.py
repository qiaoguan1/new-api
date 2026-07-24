import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from recent_actual_cost import collect_recent_model_costs  # noqa: E402


DAY = "2026-07-23"


def source(costs=None, *, complete=True):
    return {
        "collection_status": "complete" if complete else "incomplete",
        "actual_log_complete": complete,
        "per_model_real_cost": costs or {},
    }


def text_cost(input_cost, output_cost):
    return {
        "kind": "text",
        "input_cost_cny_per_m": input_cost,
        "output_cost_cny_per_m": output_cost,
    }


def fixed_cost(cost):
    return {"kind": "fixed", "cost_cny_per_call": cost}


class RecentActualCostTests(unittest.TestCase):
    def test_uses_newest_valid_sample_per_source_then_highest_sources(self):
        ledger = {
            "days": {
                DAY: {"a": source(), "b": source()},
                "2026-07-22": {
                    "a": source({"model": text_cost(1, 4)}),
                    "b": source({"model": text_cost(2, 3)}),
                },
                "2026-07-21": {"a": source({"model": text_cost(100, 400)})},
            }
        }
        costs = collect_recent_model_costs(ledger, DAY, "model", {"a", "b"})
        self.assertEqual(costs["text_input"][0], (2.0, "b", "2026-07-22"))
        self.assertEqual(costs["text_output"][0], (4.0, "a", "2026-07-22"))
        self.assertNotIn(100.0, [item[0] for item in costs["text_input"]])

    def test_current_day_sample_wins_over_older_sample(self):
        ledger = {
            "days": {
                DAY: {"a": source({"model": fixed_cost(1.2)})},
                "2026-07-22": {"a": source({"model": fixed_cost(9.9)})},
            }
        }
        costs = collect_recent_model_costs(ledger, DAY, "model", {"a"})
        self.assertEqual(costs["fixed"], [(1.2, "a", DAY)])

    def test_any_current_day_source_prevents_stale_source_override(self):
        ledger = {
            "days": {
                DAY: {
                    "current": source({"model": text_cost(1, 6)}),
                    "stale": source(),
                },
                "2026-07-22": {
                    "stale": source({"model": text_cost(50, 400)}),
                },
            }
        }
        costs = collect_recent_model_costs(
            ledger, DAY, "model", {"current", "stale"}
        )
        self.assertEqual(costs["text_input"], [(1.0, "current", DAY)])
        self.assertEqual(costs["text_output"], [(6.0, "current", DAY)])

    def test_expired_incomplete_future_and_ineligible_samples_are_ignored(self):
        ledger = {
            "days": {
                DAY: {"a": source(), "b": source(), "disabled": source()},
                "2026-07-22": {"a": source({"model": fixed_cost(1)}, complete=False)},
                "2026-07-16": {"b": source({"model": fixed_cost(2)})},
                "2026-07-24": {"a": source({"model": fixed_cost(3)})},
                "2026-07-21": {"disabled": source({"model": fixed_cost(4)})},
            }
        }
        costs = collect_recent_model_costs(ledger, DAY, "model", {"a", "b"})
        self.assertEqual(costs["kinds"], set())

    def test_target_day_must_be_complete_for_source(self):
        ledger = {
            "days": {
                DAY: {"a": source(complete=False)},
                "2026-07-22": {"a": source({"model": fixed_cost(1)})},
            }
        }
        costs = collect_recent_model_costs(ledger, DAY, "model", {"a"})
        self.assertEqual(costs["fixed"], [])

    def test_rejects_partial_nonpositive_and_mixed_kinds(self):
        ledger = {
            "days": {
                DAY: {"a": source(), "b": source()},
                "2026-07-22": {
                    "a": source({"model": text_cost(1, 0)}),
                    "b": source({"model": {"kind": "fixed", "cost_cny_per_call": -1}}),
                },
            }
        }
        costs = collect_recent_model_costs(ledger, DAY, "model", {"a", "b"})
        self.assertEqual(costs["kinds"], set())

    def test_validates_window(self):
        ledger = {"days": {DAY: {}}}
        for value in (0, 32, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                collect_recent_model_costs(ledger, DAY, "model", set(), lookback_days=value)


if __name__ == "__main__":
    unittest.main()
