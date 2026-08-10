import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from video_catalog_policy import (  # noqa: E402
    CatalogPolicyError,
    build_manifests,
    normalize_model_name,
    propose_model_candidate,
    validate_policy,
)


def policy_fixture():
    return {
        "schema_version": 1,
        "revision": "2026-08-09.1",
        "stable_catalog": {
            "seedance-2.0": ["480p", "720p", "1080p"],
            "seedance-2.0-fast": ["480p", "720p"],
            "seedance-2.0-mini": ["480p", "720p"],
        },
        "publish_allowlist": [
            {"model": "seedance-2.0", "resolution": "720p"},
        ],
        "rules": [
            {
                "id": "legacy-premium-720p",
                "version": 1,
                "priority": 100,
                "enabled": True,
                "review_state": "approved",
                "source": "*",
                "match": "exact",
                "pattern": "value-sd-premium-720p",
                "stable_model": "seedance-2.0",
                "resolution": "720p",
                "reason": "Reviewed legacy relay alias",
            },
            {
                "id": "paisio-selfsur-720p",
                "version": 1,
                "priority": 200,
                "enabled": True,
                "review_state": "approved",
                "source": "paisio",
                "match": "exact",
                "pattern": "seedance2.0-selfsur-720p",
                "stable_model": "seedance-2.0",
                "resolution": "720p",
                "reason": "Manually verified provider route",
            },
            {
                "id": "unapproved-discount",
                "version": 1,
                "priority": 300,
                "enabled": True,
                "review_state": "pending",
                "source": "*",
                "match": "exact",
                "pattern": "seedance-discount-720p",
                "stable_model": "seedance-2.0",
                "resolution": "720p",
                "reason": "AI suggestion only",
            },
        ],
    }


def official_fixture():
    with (MODULE_ROOT / "config" / "official-video-pricing.json").open(
        "r", encoding="utf-8"
    ) as handle:
        return json.load(handle)


class VideoCatalogPolicyTests(unittest.TestCase):
    def test_repository_policy_is_valid(self):
        policy_path = MODULE_ROOT / "config" / "video-model-policy.json"
        with policy_path.open("r", encoding="utf-8") as handle:
            policy = validate_policy(json.load(handle))
        self.assertEqual(policy["revision"], "2026-08-09.1")
        self.assertEqual(policy["_publish_keys"], {("seedance-2.0", "720p")})

    def test_known_legacy_alias_and_conservative_parser(self):
        policy = validate_policy(policy_fixture())
        alias = normalize_model_name("rolldek", "value-sd-premium-720p", policy)
        self.assertEqual(alias["status"], "matched")
        self.assertEqual(alias["stable_model"], "seedance-2.0")
        self.assertEqual(alias["resolution"], "720p")
        self.assertEqual(alias["match_type"], "global_exact")

        parsed = normalize_model_name("rolldek", "seedance-2.0-fast-480p", policy)
        self.assertEqual(parsed["status"], "matched")
        self.assertEqual(parsed["stable_model"], "seedance-2.0-fast")
        self.assertEqual(parsed["resolution"], "480p")
        self.assertEqual(parsed["match_type"], "conservative_parser")

    def test_source_exact_rule_wins_and_unreviewed_rule_never_matches(self):
        policy = validate_policy(policy_fixture())
        source_match = normalize_model_name("paisio", "seedance2.0-selfsur-720p", policy)
        self.assertEqual(source_match["status"], "matched")
        self.assertEqual(source_match["match_type"], "source_exact")

        other_source = normalize_model_name("rolldek", "seedance2.0-selfsur-720p", policy)
        self.assertEqual(other_source["status"], "review_required")
        pending = normalize_model_name("paisio", "seedance-discount-720p", policy)
        self.assertEqual(pending["status"], "review_required")

    def test_ambiguous_unknown_and_invalid_variants_fail_closed(self):
        policy = validate_policy(policy_fixture())
        cases = [
            "seedance-2.0-fast-mini-720p",
            "seedance-2.0-official2-720p",
            "seedance-2.0-fast",
            "seedance-2.5-720p",
            "something-video-720p",
            "seedance-2.0-mini-1080p",
        ]
        for raw_name in cases:
            with self.subTest(raw_name=raw_name):
                result = normalize_model_name("new-source", raw_name, policy)
                self.assertNotEqual(result["status"], "matched")

    def test_conflicting_approved_exact_rules_are_rejected(self):
        raw = policy_fixture()
        conflict = deepcopy(raw["rules"][0])
        conflict.update(
            {
                "id": "conflict",
                "stable_model": "seedance-2.0-fast",
                "resolution": "720p",
            }
        )
        raw["rules"].append(conflict)
        with self.assertRaises(CatalogPolicyError):
            validate_policy(raw)

    def test_potentially_catastrophic_operator_regex_is_rejected(self):
        raw = policy_fixture()
        raw["rules"].append(
            {
                "id": "unsafe-regex",
                "version": 1,
                "priority": 1,
                "enabled": True,
                "review_state": "approved",
                "source": "*",
                "match": "regex",
                "pattern": "(seedance+)+$",
                "stable_model": "seedance-2.0",
                "resolution": "720p",
                "reason": "must be rejected",
            }
        )
        with self.assertRaises(CatalogPolicyError):
            validate_policy(raw)

    def test_ambiguous_provider_marker_gets_untrusted_review_suggestion(self):
        suggestion = propose_model_candidate(
            "rolldek",
            "seedance-2.0-fast-431-480p",
            validate_policy(policy_fixture()),
        )
        self.assertEqual(suggestion["stable_model"], "seedance-2.0-fast")
        self.assertEqual(suggestion["resolution"], "480p")
        self.assertEqual(suggestion["confidence"], "low")
        self.assertTrue(suggestion["requires_review"])

        self.assertIsNone(
            propose_model_candidate(
                "paisio",
                "seedance-2.5-720p",
                validate_policy(policy_fixture()),
            )
        )

    def test_publish_manifest_is_strict_intersection_and_privacy_safe(self):
        policy = validate_policy(policy_fixture())
        mappings = [
            {
                **normalize_model_name("paisio", "seedance2.0-selfsur-720p", policy),
                "source": "paisio",
                "raw_model": "seedance2.0-selfsur-720p",
            },
            {
                **normalize_model_name("rolldek", "seedance-2.0-720p", policy),
                "source": "rolldek",
                "raw_model": "seedance-2.0-720p",
            },
            {
                **normalize_model_name("rolldek", "seedance-2.0-fast-720p", policy),
                "source": "rolldek",
                "raw_model": "seedance-2.0-fast-720p",
            },
        ]
        routes = [
            {
                "source": "paisio",
                "raw_model": "seedance2.0-selfsur-720p",
                "enabled": True,
                "healthy": True,
            },
            {
                "source": "rolldek",
                "raw_model": "seedance-2.0-720p",
                "enabled": True,
                "healthy": False,
            },
            {
                "source": "rolldek",
                "raw_model": "seedance-2.0-fast-720p",
                "enabled": True,
                "healthy": True,
            },
        ]
        costs = [
            {
                "source": "paisio",
                "raw_model": "seedance2.0-selfsur-720p",
                "trusted": True,
                "version": "actual-2026-08-08",
                "billing_unit": "second",
                "unit_cost_cny": 0.29,
            },
            {
                "source": "rolldek",
                "raw_model": "seedance-2.0-720p",
                "trusted": True,
                "version": "actual-2026-08-08",
                "billing_unit": "call",
                "unit_cost_cny": 1.10,
            },
            {
                "source": "rolldek",
                "raw_model": "seedance-2.0-fast-720p",
                "trusted": True,
                "version": "actual-2026-08-08",
                "billing_unit": "call",
                "unit_cost_cny": 0.80,
            },
        ]

        manifests = build_manifests(
            mappings, routes, costs, policy, official_fixture()
        )
        self.assertEqual(len(manifests["internal"]["routes"]), 1)
        self.assertEqual(
            manifests["internal"]["routes"][0]["upstream_cost"]["unit_cost_cny"],
            0.29,
        )
        self.assertEqual(
            manifests["internal"]["routes"][0]["profit_comparison"][
                "profit_cny_per_second"
            ],
            1.2004,
        )
        self.assertEqual(
            manifests["public"],
            {
                "protocol": "xtai-relay-v1",
                "catalog_revision": "2026-08-09.1",
                "pricing_revision": "2026-08-09.1",
                "models": [
                    {
                        "id": "seedance-2.0",
                        "resolutions": ["720p"],
                        "available": True,
                        "pricing_revision": "2026-08-09.1",
                        "pricing": {
                            "720p": {
                                "billing_unit": "output_second",
                                "sale_cny_per_second_16_9": 1.4904,
                            }
                        },
                    }
                ],
            },
        )
        public_text = repr(manifests["public"])
        self.assertNotIn("paisio", public_text)
        self.assertNotIn("selfsur", public_text)
        self.assertNotIn("cost", public_text)
        self.assertNotIn("markup", public_text)
        self.assertNotIn("margin", public_text)

    def test_missing_upstream_cost_does_not_block_officially_priced_route(self):
        policy = validate_policy(policy_fixture())
        mapping = {
            **normalize_model_name("rolldek", "seedance-2.0-720p", policy),
            "source": "rolldek",
            "raw_model": "seedance-2.0-720p",
        }
        route = {
            "source": "rolldek",
            "raw_model": "seedance-2.0-720p",
            "enabled": True,
            "healthy": True,
        }
        manifests = build_manifests(
            [mapping], [route], [], policy, official_fixture()
        )
        self.assertEqual(len(manifests["internal"]["routes"]), 1)
        self.assertNotIn("upstream_cost", manifests["internal"]["routes"][0])
        self.assertEqual(manifests["public"]["models"][0]["id"], "seedance-2.0")

    def test_cost_from_different_raw_model_is_never_borrowed(self):
        policy = validate_policy(policy_fixture())
        mapping = {
            **normalize_model_name("paisio", "seedance2.0-selfsur-720p", policy),
            "source": "paisio",
            "raw_model": "seedance2.0-selfsur-720p",
        }
        manifests = build_manifests(
            [mapping],
            [
                {
                    "source": "paisio",
                    "raw_model": "seedance2.0-selfsur-720p",
                    "enabled": True,
                    "healthy": True,
                }
            ],
            [
                {
                    "source": "paisio",
                    "stable_model": "seedance-2.0",
                    "resolution": "720p",
                    "raw_model": "sd2-720p",
                    "trusted": True,
                    "version": "actual:2026-08-08",
                    "billing_unit": "second",
                    "unit_cost_cny": 0.29,
                }
            ],
            policy,
            official_fixture(),
        )
        self.assertEqual(len(manifests["internal"]["routes"]), 1)
        self.assertNotIn("upstream_cost", manifests["internal"]["routes"][0])
        self.assertEqual(manifests["public"]["models"][0]["id"], "seedance-2.0")


if __name__ == "__main__":
    unittest.main()
