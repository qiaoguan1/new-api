import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply-official-video-pricing.py"
for path in (ROOT, SCRIPT.parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
SPEC = importlib.util.spec_from_file_location("apply_official_video_pricing", SCRIPT)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(worker)


class OfficialRouteTests(unittest.TestCase):
    def policy(self):
        return {
            "schema_version": 1,
            "revision": "test",
            "stable_catalog": {
                "seedance-2.0": ["480p", "720p", "1080p"],
                "seedance-2.0-fast": ["480p", "720p"],
                "seedance-2.0-mini": ["480p", "720p"],
            },
            "publish_allowlist": [],
            "rules": [],
        }

    def test_routes_join_by_exact_channel_source_and_raw_model(self):
        policy = {
            "schema_version": 1,
            "revision": "test",
            "stable_catalog": {
                "seedance-2.0": ["480p", "720p", "1080p"],
                "seedance-2.0-fast": ["480p", "720p"],
                "seedance-2.0-mini": ["480p", "720p"],
            },
            "publish_allowlist": [],
            "rules": [
                {
                    "id": "paisio-same",
                    "version": 1,
                    "priority": 100,
                    "enabled": True,
                    "review_state": "approved",
                    "source": "paisio",
                    "match": "exact",
                    "pattern": "same-name",
                    "stable_model": "seedance-2.0",
                    "resolution": "720p",
                    "reason": "test",
                },
                {
                    "id": "rolldek-same",
                    "version": 1,
                    "priority": 100,
                    "enabled": True,
                    "review_state": "approved",
                    "source": "rolldek",
                    "match": "exact",
                    "pattern": "same-name",
                    "stable_model": "seedance-2.0-fast",
                    "resolution": "720p",
                    "reason": "test",
                },
            ],
        }
        report = {
            "policy_revision": "test",
            "matched": [
                {
                    "channel_id": 42,
                    "source": "paisio",
                    "raw_model": "same-name",
                    "status": "matched",
                    "stable_model": "seedance-2.0",
                    "resolution": "720p",
                },
                {
                    "channel_id": 45,
                    "source": "rolldek",
                    "raw_model": "same-name",
                    "status": "matched",
                    "stable_model": "seedance-2.0-fast",
                    "resolution": "720p",
                },
            ]
        }
        audit = {
            "date": "2026-08-08",
            "channels": [
                {
                    "channel_id": 42,
                    "status": 1,
                    "upstream_slug": "paisio",
                    "configured_models": ["same-name"],
                }
            ],
        }

        routes = worker.build_official_routes(
            report, audit, policy, expected_day="2026-08-08"
        )

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["stable_model"], "seedance-2.0")

    def test_stale_mapping_report_fails_and_unmapped_non_protocol_models_are_ignored(self):
        with self.assertRaisesRegex(worker.OfficialVideoPricingError, "revision"):
            worker.build_official_routes(
                {"policy_revision": "old", "matched": []},
                {"date": "2026-08-08", "channels": []},
                self.policy(),
                expected_day="2026-08-08",
            )

        routes = worker.build_official_routes(
            {"policy_revision": "test", "matched": []},
            {
                "date": "2026-08-08",
                "channels": [
                    {
                        "channel_id": 42,
                        "status": 1,
                        "upstream_slug": "paisio",
                        "configured_models": ["seedance-new-720p"],
                    }
                ],
            },
            self.policy(),
            expected_day="2026-08-08",
        )
        self.assertEqual(routes, [])

        with self.assertRaisesRegex(worker.OfficialVideoPricingError, "missing"):
            worker.build_official_routes(
                {"policy_revision": "test", "matched": []},
                {
                    "date": "2026-08-08",
                    "channels": [
                        {
                            "channel_id": 45,
                            "status": 1,
                            "upstream_slug": "rolldek",
                            "configured_models": ["seedance-2.0-720p"],
                        }
                    ],
                },
                self.policy(),
                expected_day="2026-08-08",
            )

    def test_reviewed_exact_alias_remains_priced_when_upstream_catalog_drops_it(self):
        policy = self.policy()
        policy["rules"] = [
            {
                "id": "paisio-reviewed-alias",
                "version": 1,
                "priority": 100,
                "enabled": True,
                "review_state": "approved",
                "source": "paisio",
                "match": "exact",
                "pattern": "seedance2.0-selfsur-720p",
                "stable_model": "seedance-2.0",
                "resolution": "720p",
                "reason": "manually reviewed production alias",
            }
        ]
        audit = {
            "date": "2026-08-08",
            "channels": [
                {
                    "channel_id": 42,
                    "status": 1,
                    "upstream_slug": "paisio",
                    "configured_models": ["seedance2.0-selfsur-720p"],
                    "unavailable_models": {
                        "seedance2.0-selfsur-720p": {
                            "reason": "not_in_upstream_pricing_catalog"
                        }
                    },
                }
            ],
        }

        routes = worker.build_official_routes(
            {"policy_revision": "test", "matched": []},
            audit,
            policy,
            expected_day="2026-08-08",
        )

        self.assertEqual(
            routes,
            [
                {
                    "channel_id": 42,
                    "source": "paisio",
                    "raw_model": "seedance2.0-selfsur-720p",
                    "stable_model": "seedance-2.0",
                    "resolution": "720p",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
