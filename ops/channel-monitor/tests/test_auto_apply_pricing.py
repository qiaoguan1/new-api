import importlib.util
import math
import pathlib
import subprocess
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "auto-apply-pricing.py"
SPEC = importlib.util.spec_from_file_location("auto_apply_pricing", SCRIPT_PATH)
pricing = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pricing)


DAY = "2026-07-22"


def audit(*channels, alerts=None):
    return {
        "date": DAY,
        "channels": list(channels),
        "alerts": list(alerts or []),
    }


def channel(channel_id, slug, models, *, status=1, scan_status="ok"):
    return {
        "channel_id": channel_id,
        "status": status,
        "scan_status": scan_status,
        "pricing_status": "ok",
        "upstream_slug": slug,
        "models": {name: {"available": True} for name in models},
    }


def ledger(**sources):
    return {"days": {DAY: sources}}


def source(**models):
    return {
        "collection_status": "complete",
        "actual_log_complete": True,
        "per_model_real_cost": models,
    }


def text_cost(input_cost, output_cost):
    return {
        "kind": "text",
        "input_cost_cny_per_m": input_cost,
        "output_cost_cny_per_m": output_cost,
    }


def fixed_cost(cost):
    return {"kind": "fixed", "cost_cny_per_call": cost}


class PricingPlanTests(unittest.TestCase):
    def current_options(self):
        return {
            "ModelRatio": {},
            "CompletionRatio": {},
            "ModelPrice": {},
            "GroupRatio": {"text": 0.15, "image": 0.15, "video": 0.15},
        }

    def test_discovers_unknown_text_and_fixed_models_without_allowlist(self):
        daily_audit = audit(channel(1, "healthy", ["brand-new-text", "brand-new-video"]))
        daily_ledger = ledger(
            healthy=source(
                **{
                    "brand-new-text": text_cost(2.0, 8.0),
                    "brand-new-video": fixed_cost(0.8),
                }
            )
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )

        decisions = {item["model"]: item for item in result["decisions"]}
        self.assertEqual(decisions["brand-new-text"]["action"], "apply")
        self.assertEqual(decisions["brand-new-video"]["action"], "apply")

    def test_explicit_configured_inventory_excludes_unrelated_catalog_models(self):
        source_channel = channel(
            41,
            "nodyhub",
            ["grok-video-3", "unrelated-upstream-catalog-model"],
        )
        source_channel["configured_models"] = ["grok-video-3"]

        policy = pricing.build_audit_policy(audit(source_channel), DAY)

        self.assertEqual(policy["discovered_models"], {"grok-video-3"})
        self.assertEqual(policy["model_sources"], {"grok-video-3": {"nodyhub"}})

    def test_text_and_fixed_prices_equal_actual_cost_times_one_point_five(self):
        daily_audit = audit(channel(1, "a", ["text-model", "image-model"]))
        daily_ledger = ledger(
            a=source(
                **{
                    "text-model": text_cost(2.0, 7.0),
                    "image-model": {"kind": "image", "cost_cny_per_image": 0.4},
                }
            )
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )
        decisions = {item["model"]: item for item in result["decisions"]}

        text = decisions["text-model"]
        self.assertTrue(math.isclose(text["input_sell_cny_per_m"], 3.0))
        self.assertTrue(math.isclose(text["output_sell_cny_per_m"], 10.5))
        self.assertTrue(math.isclose(result["options"]["ModelRatio"]["text-model"], 10.0))
        self.assertTrue(math.isclose(result["options"]["CompletionRatio"]["text-model"], 3.5))

        fixed = decisions["image-model"]
        self.assertTrue(math.isclose(fixed["sell_cny_per_call"], 0.6))
        self.assertTrue(math.isclose(result["options"]["ModelPrice"]["image-model"], 4.0))

    def test_disabled_and_failed_channels_cannot_influence_highest_cost(self):
        daily_audit = audit(
            channel(1, "healthy", ["model"]),
            channel(2, "disabled", ["model"], status=2),
            channel(3, "failed", ["model"], scan_status="error"),
        )
        daily_ledger = ledger(
            healthy=source(model=text_cost(1.0, 4.0)),
            disabled=source(model=text_cost(100.0, 400.0)),
            failed=source(model=text_cost(200.0, 800.0)),
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )
        decision = result["decisions"][0]

        self.assertEqual(decision["action"], "apply")
        self.assertEqual(decision["worst_input_source"], "healthy")
        self.assertEqual(decision["worst_input_cost_cny_per_m"], 1.0)

    def test_discovered_model_without_actual_cost_is_skipped_and_preserved(self):
        daily_audit = audit(channel(1, "healthy", ["unused-model"]))
        options = self.current_options()
        options["ModelRatio"]["unused-model"] = 9.0

        result = pricing.build_pricing_plan(
            ledger(healthy=source()), daily_audit, DAY, options, max_change_ratio=5.0
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "no_trusted_actual_cost")
        self.assertEqual(result["options"]["ModelRatio"]["unused-model"], 9.0)

    def test_incomplete_upstream_collection_blocks_every_affected_model(self):
        daily_audit = audit(
            channel(1, "complete", ["shared-model"]),
            channel(2, "captcha", ["shared-model", "captcha-only-model"]),
        )
        incomplete = {
            "collection_status": "incomplete",
            "actual_log_complete": False,
            "collection_error": "captcha required",
            "per_model_real_cost": {},
        }
        daily_ledger = ledger(
            complete=source(**{"shared-model": text_cost(1.0, 4.0)}),
            captcha=incomplete,
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )
        decisions = {item["model"]: item for item in result["decisions"]}

        self.assertEqual(decisions["shared-model"]["action"], "skip")
        self.assertEqual(decisions["shared-model"]["reason"], "upstream_collection_incomplete")
        self.assertEqual(decisions["shared-model"]["incomplete_sources"], ["captcha"])
        self.assertEqual(decisions["captcha-only-model"]["reason"], "upstream_collection_incomplete")

    def test_missing_dated_entry_is_incomplete_not_zero_cost(self):
        result = pricing.build_pricing_plan(
            ledger(),
            audit(channel(1, "missing", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        self.assertEqual(result["decisions"][0]["reason"], "upstream_collection_incomplete")
        self.assertEqual(result["decisions"][0]["incomplete_sources"], ["missing"])

    def test_incomplete_failed_scan_source_still_blocks_shared_model(self):
        daily_audit = audit(
            channel(1, "healthy", ["model"]),
            channel(2, "failed", ["model"], scan_status="error"),
        )
        result = pricing.build_pricing_plan(
            ledger(
                healthy=source(model=text_cost(1.0, 2.0)),
                failed={"collection_status": "incomplete", "actual_log_complete": False},
            ),
            daily_audit,
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        self.assertEqual(result["decisions"][0]["reason"], "upstream_collection_incomplete")
        self.assertEqual(result["decisions"][0]["incomplete_sources"], ["failed"])

    def test_global_credential_gate_requires_every_account_collection(self):
        daily_ledger = ledger(
            good=source(),
            bad={"collection_status": "incomplete", "actual_log_complete": False},
        )

        self.assertEqual(
            pricing.incomplete_credential_sources(
                daily_ledger, DAY, {"good": {}, "bad": {}}
            ),
            ["bad"],
        )

    def test_stale_audit_and_group_ratio_mismatch_fail_closed(self):
        stale = audit(channel(1, "healthy", ["model"]))
        stale["date"] = "2026-07-21"
        daily_ledger = ledger(healthy=source(model=text_cost(1.0, 2.0)))

        with self.assertRaises(pricing.PricingError):
            pricing.build_pricing_plan(
                daily_ledger, stale, DAY, self.current_options(), max_change_ratio=5.0
            )

        options = self.current_options()
        options["GroupRatio"]["video"] = 0.2
        with self.assertRaises(pricing.PricingError):
            pricing.build_pricing_plan(
                daily_ledger,
                audit(channel(1, "healthy", ["model"])),
                DAY,
                options,
                max_change_ratio=5.0,
            )

        with self.assertRaises(pricing.PricingError):
            pricing.build_pricing_plan(
                daily_ledger,
                audit(channel(1, "healthy", ["model"])),
                DAY,
                self.current_options(),
                max_change_ratio=float("nan"),
            )

    def test_critical_channel_alert_excludes_its_cost(self):
        daily_audit = audit(
            channel(1, "healthy", ["model"]),
            channel(2, "blocked", ["model"]),
            alerts=[{"severity": "critical", "channel_id": 2, "type": "availability_failed"}],
        )
        daily_ledger = ledger(
            healthy=source(model=text_cost(1.0, 2.0)),
            blocked=source(model=text_cost(50.0, 100.0)),
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )

        self.assertEqual(result["decisions"][0]["worst_input_source"], "healthy")

    def test_critical_model_alert_does_not_block_unrelated_channel_models(self):
        daily_audit = audit(
            channel(41, "video", ["overpriced-model", "healthy-video"]),
            alerts=[{
                "severity": "critical",
                "channel_id": 41,
                "model": "overpriced-model",
                "type": "price_below_upstream_input",
            }],
        )
        daily_ledger = ledger(
            video=source(
                **{
                    "overpriced-model": fixed_cost(100.0),
                    "healthy-video": fixed_cost(0.8),
                }
            )
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )
        decisions = {item["model"]: item for item in result["decisions"]}

        self.assertEqual(decisions["overpriced-model"]["reason"], "critical_model_alert")
        self.assertEqual(decisions["healthy-video"]["action"], "apply")

    def test_excessive_movement_is_skipped(self):
        options = self.current_options()
        options["ModelRatio"]["model"] = 1.0
        options["CompletionRatio"]["model"] = 2.0

        result = pricing.build_pricing_plan(
            ledger(healthy=source(model=text_cost(10.0, 20.0))),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            options,
            max_change_ratio=1.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "price_change_limit")
        self.assertEqual(result["options"]["ModelRatio"]["model"], 1.0)


class DatabaseUpdateTests(unittest.TestCase):
    def test_atomic_update_rejects_command_failure(self):
        completed = subprocess.CompletedProcess(["psql"], 1, stdout="", stderr="failed")
        with mock.patch.object(pricing.subprocess, "run", return_value=completed):
            with self.assertRaises(pricing.PricingError):
                pricing.atomic_update_options({"ModelRatio": {}, "CompletionRatio": {}, "ModelPrice": {}})

    def test_atomic_update_requires_three_affected_rows_and_commit(self):
        completed = subprocess.CompletedProcess(
            ["psql"], 0, stdout="BEGIN\npg_advisory_xact_lock\nDO\nCOMMIT\n", stderr=""
        )
        with mock.patch.object(pricing.subprocess, "run", return_value=completed) as run:
            output = pricing.atomic_update_options(
                {"ModelRatio": {"a": 1}, "CompletionRatio": {"a": 2}, "ModelPrice": {}}
            )
        self.assertIn("COMMIT", output)
        sql = run.call_args.args[0][-1]
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertEqual(sql.count("GET DIAGNOSTICS affected_rows = ROW_COUNT"), 3)
        self.assertEqual(sql.count("RAISE EXCEPTION"), 3)

        incomplete = subprocess.CompletedProcess(
            ["psql"], 0, stdout="BEGIN\nCOMMIT\n", stderr=""
        )
        with mock.patch.object(pricing.subprocess, "run", return_value=incomplete):
            with self.assertRaises(pricing.PricingError):
                pricing.atomic_update_options(
                    {"ModelRatio": {}, "CompletionRatio": {}, "ModelPrice": {}}
                )


if __name__ == "__main__":
    unittest.main()
