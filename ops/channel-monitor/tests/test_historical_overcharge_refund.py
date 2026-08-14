import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "historical-overcharge-refund.py"
SPEC = importlib.util.spec_from_file_location("historical_overcharge_refund", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def text_cost(input_cost="0.5", output_cost="3.0", calls=2):
    return {
        "kind": "text",
        "calls": calls,
        "input_cost_cny_per_m": input_cost,
        "output_cost_cny_per_m": output_cost,
    }


def fixed_cost(cost="0.0618", calls=2):
    return {"kind": "fixed", "calls": calls, "cost_cny_per_call": cost}


def source(costs=None, *, complete=True):
    return {
        "collection_status": "complete" if complete else "incomplete",
        "actual_log_complete": complete,
        "day_log_rows": sum(int(row.get("calls") or 0) for row in (costs or {}).values()),
        "per_model_real_cost": costs or {},
    }


def log(log_id=1, *, channel_id=39, model="gpt-5.6-sol", quota=3_750_000,
        prompt=1_000_000, completion=0, other=None):
    return {
        "id": log_id,
        "user_id": 95,
        "username": "xt-test",
        "token_id": 7,
        "channel_id": channel_id,
        "created_at": 1_786_594_717,
        "beijing_date": "2026-08-13",
        "model_name": model,
        "quota": quota,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "request_id": f"req-{log_id}",
        "other_md5": "abc",
        "other": other or {"model_ratio": 25, "completion_ratio": 8},
    }


class SourceBoundEvidenceTests(unittest.TestCase):
    def test_unrelated_incomplete_source_does_not_block_complete_actual_source(self):
        ledger = {"days": {"2026-08-13": {
            "codeplan": source({"gpt-5.6-sol": text_cost()}),
            "jojocode": source(complete=False),
        }}}
        evidence = MODULE.build_evidence_index(ledger)
        plan = MODULE.build_refund_plan(
            [log()], evidence, {39: "codeplan"}, already_refunded=set()
        )
        self.assertEqual(plan["totals"]["refund_requests"], 1)
        self.assertEqual(plan["refunds"][0]["evidence"]["source"], "codeplan")
        self.assertEqual(plan["refunds"][0]["channel_id"], 39)

    def test_incomplete_actual_source_stays_blocked(self):
        ledger = {"days": {"2026-08-13": {
            "codeplan": source({"gpt-5.6-sol": text_cost()}),
            "jojocode": source(complete=False),
        }}}
        plan = MODULE.build_refund_plan(
            [log(channel_id=30)], MODULE.build_evidence_index(ledger),
            {30: "jojocode", 39: "codeplan"}, already_refunded=set()
        )
        self.assertEqual(plan["totals"]["refund_requests"], 0)
        self.assertEqual(plan["skipped"][0]["reason"], "missing_actual_cost")
        self.assertEqual(plan["skipped"][0]["source"], "jojocode")

    def test_never_borrows_cost_from_another_source(self):
        ledger = {"days": {"2026-08-13": {
            "maolao": source({"gpt-5.6-sol": text_cost("0.1", "0.6")}),
        }}}
        plan = MODULE.build_refund_plan(
            [log()], MODULE.build_evidence_index(ledger), {39: "codeplan"}, set()
        )
        self.assertEqual(plan["totals"]["evaluated_with_evidence"], 0)
        self.assertEqual(plan["totals"]["missing_evidence"], 1)

    def test_fixed_image_refund_uses_same_source_daily_cost(self):
        ledger = {"days": {"2026-08-13": {
            "maolao": source({"gpt-image-2": fixed_cost()}),
        }}}
        item = log(
            channel_id=23, model="gpt-image-2", quota=77_250,
            prompt=0, other={"is_task": True, "model_price": 1.03},
        )
        plan = MODULE.build_refund_plan(
            [item], MODULE.build_evidence_index(ledger), {23: "maolao"}, set()
        )
        self.assertEqual(plan["refunds"][0]["policy_quota"], 46_350)
        self.assertEqual(plan["refunds"][0]["refund_quota"], 30_900)

    def test_unverified_variable_fixed_cost_source_fails_closed(self):
        ledger = {"days": {"2026-08-13": {
            "codeplan": source({"gpt-image-2": fixed_cost("0.090309")}),
        }}}
        item = log(
            channel_id=38, model="gpt-image-2", quota=77_250,
            prompt=0, other={"is_task": True, "model_price": 1.03},
        )
        plan = MODULE.build_refund_plan(
            [item], MODULE.build_evidence_index(ledger), {38: "codeplan"}, set()
        )
        self.assertEqual(plan["refunds"], [])
        self.assertEqual(plan["skipped"][0]["reason"], "missing_actual_cost")

    def test_existing_source_log_is_idempotently_skipped(self):
        ledger = {"days": {"2026-08-13": {
            "codeplan": source({"gpt-5.6-sol": text_cost()}),
        }}}
        plan = MODULE.build_refund_plan(
            [log(log_id=44)], MODULE.build_evidence_index(ledger),
            {39: "codeplan"}, {44}
        )
        self.assertEqual(plan["totals"]["already_refunded"], 1)
        self.assertEqual(plan["refunds"], [])

    def test_unmapped_channel_fails_closed(self):
        ledger = {"days": {"2026-08-13": {
            "codeplan": source({"gpt-5.6-sol": text_cost()}),
        }}}
        plan = MODULE.build_refund_plan(
            [log(channel_id=999)], MODULE.build_evidence_index(ledger), {}, set()
        )
        self.assertEqual(plan["skipped"][0]["reason"], "unmapped_channel")

    def test_video_settlement_source_is_never_refunded_from_daily_averages(self):
        ledger = {"days": {"2026-08-13": {
            "paisio": source({"sd2-720p": fixed_cost("0.168387")}),
        }}}
        item = log(
            channel_id=42, model="sd2-720p", quota=4_500_000,
            prompt=0, other={"is_task": True, "model_price": 60},
        )
        plan = MODULE.build_refund_plan(
            [item], MODULE.build_evidence_index(ledger), {42: "paisio"}, set()
        )
        self.assertEqual(plan["refunds"], [])
        self.assertEqual(plan["skipped"][0]["reason"], "video_settlement_only")
        self.assertEqual(plan["totals"]["video_settlement_only"], 1)


class ChannelInventoryTests(unittest.TestCase):
    def test_build_channel_source_map_rejects_ambiguous_channel(self):
        audit = {"channels": [
            {"channel_id": 39, "upstream_slug": "codeplan"},
            {"channel_id": 39, "upstream_slug": "maolao"},
        ]}
        with self.assertRaises(MODULE.RefundError):
            MODULE.build_channel_source_map(audit)

    def test_empty_slug_is_not_a_valid_mapping(self):
        audit = {"channels": [
            {"channel_id": 1, "upstream_slug": ""},
            {"channel_id": 39, "upstream_slug": "codeplan"},
        ]}
        self.assertEqual(MODULE.build_channel_source_map(audit), {39: "codeplan"})


class AuditContractTests(unittest.TestCase):
    def test_channel_id_changes_source_fingerprint(self):
        first = log(channel_id=39)
        second = dict(first, channel_id=20)
        self.assertNotEqual(MODULE._fingerprint(first), MODULE._fingerprint(second))

    def test_execution_sql_is_issue_111_only(self):
        ledger = {"days": {"2026-08-13": {
            "codeplan": source({"gpt-5.6-sol": text_cost()}),
        }}}
        plan = MODULE.build_refund_plan(
            [log()], MODULE.build_evidence_index(ledger), {39: "codeplan"}, set()
        )
        sql = MODULE._execution_sql(plan, commit=False)
        self.assertIn("Issue #111 historical overcharge compensation", sql)
        self.assertIn("issue111:", sql)
        self.assertNotIn("Issue #21", sql)
        self.assertNotIn("issue21:", sql)


if __name__ == "__main__":
    unittest.main()
