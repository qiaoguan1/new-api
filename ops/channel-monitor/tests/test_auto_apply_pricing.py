import importlib.util
import json
import math
import pathlib
import subprocess
import sys
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "auto-apply-pricing.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
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


def catalog_source(*rows, group="default", group_ratio=0.2, rate=7.0, **models):
    return {
        "collection_status": "incomplete",
        "actual_log_complete": False,
        "per_model_real_cost": models,
        "group": group,
        "rate": rate,
        "pricing_metadata": {
            "status": "complete",
            "group_ratio": {group: group_ratio},
            "models": list(rows),
            "account_models": [row["model_name"] for row in rows],
            "fetched_at": 1784766000,
        },
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

    def manual_catalog(self, slug, model, row, *, valid_through=DAY):
        return {
            "version": 1,
            "actual_preferred_models": [model],
            "sources": {
                slug: {
                    "models": {
                        model: {
                            **row,
                            "verified_on": DAY,
                            "valid_through": valid_through,
                        }
                    }
                }
            },
        }

    def test_discovers_unknown_text_and_image_models_without_allowlist(self):
        daily_audit = audit(channel(1, "healthy", ["brand-new-text", "brand-new-image"]))
        daily_ledger = ledger(
            healthy=source(
                **{
                    "brand-new-text": text_cost(2.0, 8.0),
                    "brand-new-image": fixed_cost(0.8),
                }
            )
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, self.current_options(), max_change_ratio=5.0
        )

        decisions = {item["model"]: item for item in result["decisions"]}
        self.assertEqual(decisions["brand-new-text"]["action"], "apply")
        self.assertEqual(decisions["brand-new-image"]["action"], "apply")

    def test_target_models_limits_both_decisions_and_option_changes(self):
        result = pricing.build_pricing_plan(
            ledger(
                healthy=source(
                    first=text_cost(1.0, 2.0),
                    second=text_cost(3.0, 6.0),
                )
            ),
            audit(channel(1, "healthy", ["first", "second"])),
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
            target_models={"first"},
        )

        self.assertEqual([item["model"] for item in result["decisions"]], ["first"])
        self.assertIn("first", result["options"]["ModelRatio"])
        self.assertNotIn("second", result["options"]["ModelRatio"])

    def test_video_model_is_never_priced_from_upstream_cost(self):
        daily_audit = audit(channel(42, "paisio", ["sd2-720p"]))
        options = self.current_options()
        options["ModelPrice"]["sd2-720p"] = 4.56

        result = pricing.build_pricing_plan(
            ledger(paisio=source(**{"sd2-720p": fixed_cost(0.168387)})),
            daily_audit,
            DAY,
            options,
            max_change_ratio=5.0,
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "video_official_pricing_only")
        self.assertEqual(result["options"]["ModelPrice"]["sd2-720p"], 4.56)

    def test_reviewed_legacy_video_alias_is_protected_by_policy(self):
        policy_path = SCRIPT_PATH.parents[1] / "config" / "video-model-policy.json"
        with policy_path.open("r", encoding="utf-8") as handle:
            policy = json.load(handle)
        daily_audit = audit(channel(42, "paisio", ["value-sd-premium-720p"]))
        protected = pricing.protected_video_models(daily_audit, policy)

        result = pricing.build_pricing_plan(
            ledger(
                paisio=source(
                    **{"value-sd-premium-720p": fixed_cost(0.1)}
                )
            ),
            daily_audit,
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
            protected_videos=protected,
        )

        self.assertEqual(protected, {"value-sd-premium-720p"})
        self.assertEqual(result["decisions"][0]["reason"], "video_official_pricing_only")

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
        self.assertEqual(result["decisions"][0]["reason"], "no_trusted_cost_evidence")
        self.assertEqual(result["options"]["ModelRatio"]["unused-model"], 9.0)

    def test_authenticated_catalog_prices_text_model_when_actual_is_unavailable(self):
        daily_ledger = ledger(
            healthy=catalog_source(
                {
                    "model_name": "catalog-text",
                    "model_ratio": 2.5,
                    "completion_ratio": 4.0,
                    "quota_type": 0,
                }
            )
        )

        result = pricing.build_pricing_plan(
            daily_ledger,
            audit(channel(1, "healthy", ["catalog-text"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["action"], "apply")
        self.assertEqual(decision["billing_kind"], "text")
        self.assertEqual(decision["cost_basis"], "authenticated_catalog")
        self.assertEqual(decision["worst_input_cost_cny_per_m"], 7.0)
        self.assertEqual(decision["worst_output_cost_cny_per_m"], 28.0)
        self.assertEqual(decision["input_sell_cny_per_m"], 10.5)
        self.assertEqual(decision["output_sell_cny_per_m"], 42.0)
        self.assertEqual(result["options"]["ModelRatio"]["catalog-text"], 35.0)
        self.assertEqual(result["options"]["CompletionRatio"]["catalog-text"], 4.0)

    def test_authenticated_catalog_prices_fixed_image_per_call(self):
        daily_ledger = ledger(
            healthy=catalog_source(
                {
                    "model_name": "catalog-image",
                    "model_price": 0.5,
                    "quota_type": 1,
                    "billing_mode": "per_call",
                }
            )
        )

        result = pricing.build_pricing_plan(
            daily_ledger,
            audit(channel(1, "healthy", ["catalog-image"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["action"], "apply")
        self.assertEqual(decision["billing_kind"], "fixed")
        self.assertEqual(decision["cost_basis"], "authenticated_catalog")
        self.assertAlmostEqual(decision["worst_cost_cny_per_call"], 0.7)
        self.assertAlmostEqual(decision["sell_cny_per_call"], 1.05)
        self.assertAlmostEqual(result["options"]["ModelPrice"]["catalog-image"], 7.0)

    def test_recent_actual_cost_precedes_higher_catalog_for_same_source(self):
        entry = catalog_source(
            {
                "model_name": "model",
                "model_ratio": 100.0,
                "completion_ratio": 8.0,
                "quota_type": 0,
            },
            model=text_cost(1.0, 4.0),
        )
        entry["collection_status"] = "complete"
        entry["actual_log_complete"] = True

        result = pricing.build_pricing_plan(
            ledger(healthy=entry),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
            manual_evidence=self.manual_catalog(
                "healthy",
                "model",
                {
                    "kind": "text",
                    "input_cost_cny_per_m": 500.0,
                    "output_cost_cny_per_m": 4000.0,
                },
            ),
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["cost_basis"], "current_day_actual")
        self.assertEqual(decision["worst_input_evidence_type"], "actual")
        self.assertEqual(decision["worst_output_evidence_type"], "actual")
        self.assertEqual(decision["worst_input_cost_cny_per_m"], 1.0)
        self.assertEqual(decision["worst_output_cost_cny_per_m"], 4.0)

    def test_unlisted_model_keeps_higher_catalog_floor(self):
        entry = catalog_source(
            {
                "model_name": "model",
                "model_ratio": 100.0,
                "completion_ratio": 8.0,
                "quota_type": 0,
            },
            model=text_cost(1.0, 4.0),
        )
        entry["collection_status"] = "complete"
        entry["actual_log_complete"] = True

        result = pricing.build_pricing_plan(
            ledger(healthy=entry),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
            manual_evidence={
                "version": 1,
                "actual_preferred_models": ["different-model"],
                "sources": {},
            },
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["cost_basis"], "mixed_actual_catalog")
        self.assertEqual(decision["worst_input_cost_cny_per_m"], 280.0)
        self.assertEqual(decision["worst_output_cost_cny_per_m"], 2240.0)

    def test_manual_authenticated_catalog_fills_source_without_actual_or_api_catalog(self):
        result = pricing.build_pricing_plan(
            ledger(verified=source()),
            audit(channel(1, "verified", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
            manual_evidence=self.manual_catalog(
                "verified",
                "model",
                {
                    "kind": "text",
                    "input_cost_cny_per_m": 0.5,
                    "output_cost_cny_per_m": 3.0,
                },
            ),
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["action"], "apply")
        self.assertEqual(decision["cost_basis"], "authenticated_catalog")
        self.assertEqual(
            decision["worst_input_evidence_type"], "manual_authenticated_catalog"
        )
        self.assertEqual(decision["input_sell_cny_per_m"], 0.75)
        self.assertEqual(decision["output_sell_cny_per_m"], 4.5)

    def test_expired_manual_catalog_fails_closed(self):
        result = pricing.build_pricing_plan(
            ledger(verified=source()),
            audit(channel(1, "verified", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
            manual_evidence=self.manual_catalog(
                "verified",
                "model",
                {
                    "kind": "fixed",
                    "cost_cny_per_call": 0.1,
                },
                valid_through="2026-07-21",
            ),
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(
            result["decisions"][0]["reason"], "no_trusted_cost_evidence"
        )

    def test_catalog_overflow_is_rejected_for_new_model_without_current_price(self):
        result = pricing.build_pricing_plan(
            ledger(
                healthy=catalog_source(
                    {
                        "model_name": "model",
                        "model_ratio": 1e308,
                        "completion_ratio": 8.0,
                        "quota_type": 0,
                    }
                )
            ),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "no_trusted_cost_evidence")

    def test_multi_source_uses_actual_per_source_then_highest_normalized_cost(self):
        result = pricing.build_pricing_plan(
            ledger(
                actual=source(model=text_cost(1.0, 4.0)),
                catalog=catalog_source(
                    {
                        "model_name": "model",
                        "model_ratio": 1.0,
                        "completion_ratio": 4.0,
                        "quota_type": 0,
                    }
                ),
            ),
            audit(
                channel(1, "actual", ["model"]),
                channel(2, "catalog", ["model"]),
            ),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["action"], "apply")
        self.assertEqual(decision["cost_basis"], "mixed_actual_catalog")
        self.assertEqual(decision["worst_input_source"], "catalog")
        self.assertAlmostEqual(decision["worst_input_cost_cny_per_m"], 2.8)
        self.assertAlmostEqual(decision["worst_output_cost_cny_per_m"], 11.2)

    def test_catalog_requires_current_authenticated_account_metadata(self):
        entry = catalog_source(
            {
                "model_name": "model",
                "model_ratio": 1.0,
                "completion_ratio": 4.0,
                "quota_type": 0,
            }
        )
        entry["pricing_metadata"]["account_models"] = []

        result = pricing.build_pricing_plan(
            ledger(healthy=entry),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "no_trusted_cost_evidence")
        self.assertEqual(result["decisions"][0]["missing_cost_sources"], ["healthy"])

        entry["pricing_metadata"]["account_models"] = ["model"]
        entry["pricing_metadata"]["fetched_at"] = 1
        result = pricing.build_pricing_plan(
            ledger(healthy=entry),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )
        self.assertEqual(result["decisions"][0]["reason"], "no_trusted_cost_evidence")

    def test_catalog_billing_kind_conflict_fails_closed(self):
        result = pricing.build_pricing_plan(
            ledger(
                text=catalog_source(
                    {
                        "model_name": "model",
                        "model_ratio": 1.0,
                        "completion_ratio": 4.0,
                        "quota_type": 0,
                    }
                ),
                fixed=catalog_source(
                    {
                        "model_name": "model",
                        "model_price": 0.5,
                        "quota_type": 1,
                        "billing_mode": "per_call",
                    }
                ),
            ),
            audit(channel(1, "text", ["model"]), channel(2, "fixed", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "ambiguous_billing_kind")

    def test_per_second_catalog_price_is_never_treated_as_text_or_fixed(self):
        result = pricing.build_pricing_plan(
            ledger(
                healthy=catalog_source(
                    {
                        "model_name": "model",
                        "model_ratio": 1.0,
                        "completion_ratio": 4.0,
                        "model_price": 0.5,
                        "quota_type": 1,
                        "billing_mode": "per_sec",
                    }
                )
            ),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "no_trusted_cost_evidence")

    def test_catalog_summary_keeps_redacted_evidence_metadata(self):
        plan = pricing.build_pricing_plan(
            ledger(
                healthy=catalog_source(
                    {
                        "model_name": "model",
                        "model_ratio": 1.0,
                        "completion_ratio": 4.0,
                        "quota_type": 0,
                    }
                )
            ),
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=50.0,
        )

        decision = pricing._summary(plan, True)["decisions"][0]
        self.assertEqual(decision["worst_input_evidence_type"], "authenticated_catalog")
        self.assertEqual(decision["worst_output_evidence_type"], "authenticated_catalog")
        self.assertEqual(decision["worst_input_catalog_fetched_at"], 1784766000)
        self.assertNotIn("pricing_metadata", decision)

    def test_recent_actual_cost_is_used_only_for_current_configured_inventory(self):
        previous_day = "2026-07-21"
        daily_ledger = {
            "days": {
                DAY: {"healthy": source()},
                previous_day: {
                    "healthy": source(model=text_cost(1.0, 4.0)),
                },
            }
        }

        result = pricing.build_pricing_plan(
            daily_ledger,
            audit(channel(1, "healthy", ["model"])),
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        decision = result["decisions"][0]
        self.assertEqual(decision["action"], "apply")
        self.assertEqual(decision["cost_basis"], "recent_actual")
        self.assertEqual(decision["worst_input_sample_date"], previous_day)

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

    def test_main_continues_to_model_plan_when_an_unrelated_credential_is_incomplete(self):
        daily_ledger = ledger(
            good=source(model=text_cost(1.0, 2.0)),
            bad={"collection_status": "incomplete", "actual_log_complete": False},
        )
        daily_audit = audit(channel(1, "good", ["model"]))
        paths = {
            pricing.LEDGER_PATH: daily_ledger,
            pricing.AUDIT_PATH: daily_audit,
            pricing.VIDEO_POLICY_PATH: {},
            pricing.MANUAL_EVIDENCE_PATH: {"version": 1, "sources": {}},
            pricing.CREDENTIALS_PATH: {"good": {}, "bad": {}},
        }
        options = self.current_options()
        plan = {
            "date": DAY,
            "group_ratio": 0.15,
            "markup": 1.5,
            "decisions": [{"model": "model", "action": "apply", "reason": "ok"}],
            "options": {key: options[key] for key in pricing.OPTION_KEYS},
        }

        with (
            mock.patch.object(pricing, "target_beijing_day", return_value=DAY),
            mock.patch.object(pricing, "read_json", side_effect=lambda path, *a, **k: paths[path]),
            mock.patch.object(pricing, "get_option", side_effect=lambda key: options[key]),
            mock.patch.object(pricing, "protected_video_models", return_value=set()),
            mock.patch.object(pricing, "build_pricing_plan", return_value=plan) as build,
            mock.patch.object(pricing, "append_run_log"),
        ):
            code = pricing.main(["--dry-run"])

        self.assertEqual(code, 0)
        build.assert_called_once()

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

    def test_underpriced_model_self_corrects_from_worst_trusted_actual_costs(self):
        options = self.current_options()
        options["ModelRatio"]["underpriced-model"] = 5.15
        options["CompletionRatio"]["underpriced-model"] = 6.0
        daily_audit = audit(
            channel(41, "a", ["underpriced-model", "healthy-model"]),
            channel(42, "b", ["underpriced-model"]),
            alerts=[{
                "severity": "critical",
                "channel_id": 41,
                "model": "underpriced-model",
                "type": "price_below_upstream_input",
            }],
        )
        daily_ledger = ledger(
            a=source(
                **{
                    "underpriced-model": text_cost(1.0, 8.0),
                    "healthy-model": text_cost(0.5, 2.0),
                }
            ),
            b=source(**{"underpriced-model": text_cost(1.03, 6.18)}),
        )

        result = pricing.build_pricing_plan(
            daily_ledger, daily_audit, DAY, options, max_change_ratio=5.0
        )
        decisions = {item["model"]: item for item in result["decisions"]}

        recovered = decisions["underpriced-model"]
        self.assertEqual(recovered["action"], "apply")
        self.assertEqual(recovered["reason"], "underpriced_self_correction")
        self.assertEqual(recovered["underpricing_alert_types"], ["price_below_upstream_input"])
        self.assertEqual(recovered["worst_input_cost_cny_per_m"], 1.03)
        self.assertEqual(recovered["worst_input_source"], "b")
        self.assertEqual(recovered["worst_output_cost_cny_per_m"], 8.0)
        self.assertEqual(recovered["worst_output_source"], "a")
        self.assertEqual(recovered["old_model_ratio"], 5.15)
        self.assertEqual(recovered["old_completion_ratio"], 6.0)
        self.assertAlmostEqual(recovered["new_model_ratio"], 5.15)
        self.assertAlmostEqual(recovered["new_completion_ratio"], 8.0 / 1.03)
        self.assertAlmostEqual(recovered["input_sell_cny_per_m"], 1.545)
        self.assertAlmostEqual(recovered["output_sell_cny_per_m"], 12.0)
        self.assertEqual(decisions["healthy-model"]["action"], "apply")

    def test_other_critical_alert_still_blocks_underpriced_model(self):
        daily_audit = audit(
            channel(41, "healthy", ["model"]),
            alerts=[
                {
                    "severity": "critical",
                    "channel_id": 41,
                    "model": "model",
                    "type": "price_below_upstream_input",
                },
                {
                    "severity": "critical",
                    "channel_id": 41,
                    "model": "model",
                    "type": "billing_kind_conflict",
                },
            ],
        )

        result = pricing.build_pricing_plan(
            ledger(healthy=source(model=text_cost(1.0, 8.0))),
            daily_audit,
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "critical_model_alert")

    def test_non_string_critical_alert_type_fails_closed_for_model(self):
        result = pricing.build_pricing_plan(
            ledger(healthy=source(model=text_cost(1.0, 8.0))),
            audit(
                channel(41, "healthy", ["model"]),
                alerts=[{
                    "severity": "critical",
                    "channel_id": 41,
                    "model": "model",
                    "type": ["price_below_upstream_input"],
                }],
            ),
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "critical_model_alert")

    def test_underpriced_recovery_still_obeys_incomplete_collection_guard(self):
        daily_audit = audit(
            channel(41, "complete", ["model"]),
            channel(42, "missing", ["model"]),
            alerts=[{
                "severity": "critical",
                "channel_id": 41,
                "model": "model",
                "type": "price_below_upstream_input",
            }],
        )

        result = pricing.build_pricing_plan(
            ledger(complete=source(model=text_cost(1.0, 8.0))),
            daily_audit,
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        self.assertEqual(result["decisions"][0]["action"], "skip")
        self.assertEqual(result["decisions"][0]["reason"], "upstream_collection_incomplete")
        self.assertEqual(result["decisions"][0]["incomplete_sources"], ["missing"])

    def test_run_summary_preserves_underpricing_recovery_evidence(self):
        plan = pricing.build_pricing_plan(
            ledger(healthy=source(model=text_cost(1.0, 8.0))),
            audit(
                channel(41, "healthy", ["model"]),
                alerts=[{
                    "severity": "critical",
                    "channel_id": 41,
                    "model": "model",
                    "type": "price_below_upstream_input",
                }],
            ),
            DAY,
            self.current_options(),
            max_change_ratio=5.0,
        )

        decision = pricing._summary(plan, True)["decisions"][0]
        self.assertEqual(decision["reason"], "underpriced_self_correction")
        self.assertEqual(decision["underpricing_alert_types"], ["price_below_upstream_input"])
        self.assertEqual(decision["worst_input_source"], "healthy")
        self.assertEqual(decision["worst_output_source"], "healthy")
        self.assertEqual(decision["new_model_ratio"], 5.0)
        self.assertEqual(decision["new_completion_ratio"], 8.0)
        self.assertEqual(decision["input_sell_cny_per_m"], 1.5)
        self.assertEqual(decision["output_sell_cny_per_m"], 12.0)

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
        options = {"ModelRatio": {}, "CompletionRatio": {}, "ModelPrice": {}}
        with mock.patch.object(pricing.subprocess, "run", return_value=completed):
            with self.assertRaises(pricing.PricingError):
                pricing.atomic_update_options(options, options)

    def test_atomic_update_requires_three_affected_rows_and_commit(self):
        completed = subprocess.CompletedProcess(
            ["psql"], 0, stdout="BEGIN\npg_advisory_xact_lock\nDO\nCOMMIT\n", stderr=""
        )
        with mock.patch.object(pricing.subprocess, "run", return_value=completed) as run:
            expected = {"ModelRatio": {"old": 1}, "CompletionRatio": {}, "ModelPrice": {}}
            output = pricing.atomic_update_options(
                {"ModelRatio": {"a": 1}, "CompletionRatio": {"a": 2}, "ModelPrice": {}},
                expected,
            )
        self.assertIn("COMMIT", output)
        sql = run.call_args.args[0][-1]
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertEqual(sql.count("GET DIAGNOSTICS affected_rows = ROW_COUNT"), 3)
        self.assertEqual(sql.count("RAISE EXCEPTION"), 3)
        self.assertEqual(sql.count("value::jsonb="), 3)
        self.assertIn('"old":1', sql)

        incomplete = subprocess.CompletedProcess(
            ["psql"], 0, stdout="BEGIN\nCOMMIT\n", stderr=""
        )
        with mock.patch.object(pricing.subprocess, "run", return_value=incomplete):
            with self.assertRaises(pricing.PricingError):
                options = {"ModelRatio": {}, "CompletionRatio": {}, "ModelPrice": {}}
                pricing.atomic_update_options(options, options)


if __name__ == "__main__":
    unittest.main()
