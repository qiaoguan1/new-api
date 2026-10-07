"""Protect actual-cost evidence and highest-retained-cost markup invariants."""

import base64
import unittest

from plan import build_plan, SOURCE_EXPRESSIONS


def evidence(source: str) -> dict:
    """Represent a successful dedicated-key probe and its matching actual bill."""
    prompt, completion, quota, group, rate = (
        (22, 23, 822, "1.2", "1") if source == "rolldek-ccmax"
        else (630, 63, 6143, "1.3", "1.03")
    )
    return {
        "source": source, "group_ratio": group, "cny_per_credit": rate,
        "expected_token_name": "xtai-fable187-probe-" + source + "-20261007",
        "probe": {"success": True, "response_model": "claude-fable-5-1",
                  "usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
        "bill": {"model_name": "claude-fable-5-1", "type": 2, "quota": quota,
                 "token_name": "xtai-fable187-probe-" + source + "-20261007",
                 "request_id": "dedicated-probe-" + source,
                 "prompt_tokens": prompt, "completion_tokens": completion,
                 "other": {"group_ratio": float(group), "billing_mode": "tiered_expr",
                           "request_multiplier": 1,
                           "expr_b64": base64.b64encode(SOURCE_EXPRESSIONS[source].encode()).decode()}},
    }


class PlanTests(unittest.TestCase):
    def test_exact_costs_and_markup_with_separate_cache_ttls(self) -> None:
        plan = build_plan([evidence("rolldek-ccmax"), evidence("maolao")])
        self.assertEqual(plan["retail_cny_per_m"]["standard"], {
            "input": "20.085", "output": "100.425", "cache_read": "0.502125",
            "cache_write_5m": "25.10625", "cache_write_1h": "40.17",
        })
        self.assertEqual(plan["retail_cny_per_m"]["max"]["input"], "60.255")
        self.assertEqual(plan["retail_cny_per_m"]["max"]["cache_write_1h"], "120.51")
        self.assertEqual(plan["options"]["ModelRatio"], 66.95)
        self.assertEqual(plan["options"]["CompletionRatio"], 5.0)
        self.assertEqual(plan["options"]["CacheRatio"], 0.025)
        self.assertEqual(plan["options"]["CreateCacheRatio"], 1.25)
        self.assertIn('p * 133.9', plan["billing_expr"])
        self.assertIn('cc1h * 267.8', plan["billing_expr"])
        self.assertIn('p * 401.7', plan["billing_expr"])
        self.assertIn('param("reasoning_effort") == "max"', plan["billing_expr"])
        self.assertEqual(plan["billing_mode"], "tiered_expr")
        self.assertIs(plan["compatibility_verified"], False)

    def test_rejects_mismatched_model_or_actual_charge(self) -> None:
        for field, bad in [("model_name", "claude-fable-5"), ("quota", 1)]:
            item = evidence("maolao")
            item["bill"][field] = bad
            with self.assertRaises(ValueError):
                build_plan([evidence("rolldek-ccmax"), item])

    def test_rejects_missing_or_changed_billing_contract(self) -> None:
        for bad in ["", 'tier("base", p * 1 + c * 5)']:
            item = evidence("maolao")
            item["bill"]["other"]["expr_b64"] = base64.b64encode(bad.encode()).decode()
            with self.assertRaises(ValueError):
                build_plan([evidence("rolldek-ccmax"), item])

    def test_rejects_group_or_currency_conversion_drift(self) -> None:
        for field, bad in [("group_ratio", "0.2"), ("cny_per_credit", "0"),
                           ("cny_per_credit", "NaN")]:
            item = evidence("rolldek-ccmax")
            item[field] = bad
            with self.assertRaises(ValueError):
                build_plan([item, evidence("maolao")])

    def test_model_identity_and_retained_policy_are_not_silently_relaxed(self) -> None:
        item = evidence("maolao")
        item["probe"]["response_model"] = "claude-opus-5-5"
        with self.assertRaises(ValueError):
            build_plan([evidence("rolldek-ccmax"), item])
        with self.assertRaises(ValueError):
            build_plan([evidence("maolao"), evidence("maolao")])

    def test_cannot_borrow_another_tokens_identical_usage_bill(self) -> None:
        item = evidence("maolao")
        item["bill"]["token_name"] = "unrelated-key"
        with self.assertRaises(ValueError):
            build_plan([evidence("rolldek-ccmax"), item])


if __name__ == "__main__":
    unittest.main()
