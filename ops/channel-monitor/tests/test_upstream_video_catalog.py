import json
import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from upstream_video_catalog import (  # noqa: E402
    CatalogCollectionError,
    build_mapping_report,
    build_route_gates,
    build_trusted_price_evidence,
    extract_openai_model_names,
    fetch_channel_catalog,
    merge_complete_snapshot,
    read_enabled_channels,
    source_for_channel,
)
from video_catalog_policy import validate_policy  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return self.responses.pop(0)


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def repository_policy():
    with (MODULE_ROOT / "config" / "video-model-policy.json").open(
        "r", encoding="utf-8"
    ) as handle:
        return validate_policy(json.load(handle))


class UpstreamVideoCatalogTests(unittest.TestCase):
    def test_enabled_channels_are_loaded_from_checked_database_json(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return FakeCompletedProcess(
                stdout=json.dumps(
                    [
                        {
                            "id": 42,
                            "name": "Paisio · 视频",
                            "type": 1,
                            "base_url": "https://api.paisio.online",
                            "key": "protected-in-memory-only",
                            "status": 1,
                            "models": "seedance2.0-selfsur-720p",
                        }
                    ]
                )
            )

        rows = read_enabled_channels("/opt/ai-api-stack", runner=runner)
        self.assertEqual([row["id"] for row in rows], [42])
        self.assertIn("WHERE status=1", calls[0][0][-1])
        self.assertTrue(calls[0][1]["capture_output"])
        self.assertFalse(calls[0][1]["check"])

    def test_database_failure_is_sanitized(self):
        secret = "sk-db-error-secret"

        def runner(*_args, **_kwargs):
            return FakeCompletedProcess(returncode=1, stderr=f"connection failed {secret}")

        with self.assertRaises(CatalogCollectionError) as raised:
            read_enabled_channels("/opt/ai-api-stack", runner=runner, secrets=[secret])
        self.assertNotIn(secret, str(raised.exception))

    def test_extracts_only_bounded_unique_openai_model_ids(self):
        payload = {
            "object": "list",
            "data": [
                {"id": "seedance-2.0-720p", "object": "model"},
                {"id": "seedance-2.0-720p"},
                {"id": "  seedance-2.0-fast-720p  "},
                {"name": "must-not-be-used"},
                {"id": "x" * 300},
                "invalid",
            ],
        }
        self.assertEqual(
            extract_openai_model_names(payload),
            ["seedance-2.0-720p", "seedance-2.0-fast-720p"],
        )

    def test_source_is_derived_from_registered_host_and_removed_sources_are_skipped(
        self,
    ):
        upstreams = [
            {
                "slug": "paisio",
                "hosts": ["paisio.online", "api.paisio.online"],
                "aliases": ["paisio"],
            },
            {"slug": "rolldek", "hosts": ["rolldek.com"], "aliases": ["rolldek"]},
        ]
        self.assertEqual(
            source_for_channel(
                {"id": 42, "name": "Paisio · 视频", "base_url": "https://api.paisio.online/v1"},
                upstreams,
            ),
            "paisio",
        )
        self.assertIsNone(
            source_for_channel(
                {"id": 90, "name": "packapi-video", "base_url": "https://pack.example"},
                upstreams,
            )
        )
        self.assertEqual(
            source_for_channel(
                {"id": 99, "name": "new video source", "base_url": "https://new.example"},
                upstreams,
            ),
            "channel-99",
        )

    def test_fetch_tries_multiple_keys_filters_candidates_and_never_returns_secrets(self):
        secret_one = "sk-first-secret"
        secret_two = "sk-second-secret"
        session = FakeSession(
            [
                FakeResponse(401, {"error": {"message": f"bad {secret_one}"}}),
                FakeResponse(
                    200,
                    {
                        "data": [
                            {"id": "gpt-5.6"},
                            {"id": "seedance-2.5-720p"},
                            {"id": "seedance-2.0-720p"},
                            {"id": "seedance-2.0-431-720p"},
                        ]
                    },
                ),
            ]
        )
        observation = fetch_channel_catalog(
            {
                "id": 45,
                "name": "Rolldek 视频",
                "base_url": "https://rolldek.com/",
                "key": f"{secret_one}\n{secret_two}",
                "status": 1,
            },
            "rolldek",
            repository_policy(),
            session=session,
            collected_at="2026-08-09T12:00:00+08:00",
        )
        self.assertTrue(observation["complete"])
        self.assertEqual(observation["catalog_count"], 4)
        self.assertEqual(
            observation["relevant_models"],
            ["seedance-2.0-431-720p", "seedance-2.0-720p"],
        )
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(call["allow_redirects"] is False for call in session.calls))
        serialized = json.dumps(observation)
        self.assertNotIn(secret_one, serialized)
        self.assertNotIn(secret_two, serialized)

    def test_insecure_upstream_url_is_rejected_before_a_secret_is_sent(self):
        session = FakeSession([])
        observation = fetch_channel_catalog(
            {
                "id": 45,
                "name": "unsafe",
                "base_url": "http://upstream.example",
                "key": "sk-do-not-send",
                "status": 1,
            },
            "unsafe",
            repository_policy(),
            session=session,
            collected_at="2026-08-09T13:00:00+08:00",
        )
        self.assertFalse(observation["complete"])
        self.assertIn("HTTPS", observation["error"])
        self.assertEqual(session.calls, [])

    def test_failed_collection_is_sanitized_and_preserves_last_complete_channel_snapshot(self):
        secret = "sk-never-log-this"
        failed = fetch_channel_catalog(
            {
                "id": 45,
                "name": "Rolldek 视频",
                "base_url": "https://rolldek.com",
                "key": secret,
                "status": 1,
            },
            "rolldek",
            repository_policy(),
            session=FakeSession(
                [FakeResponse(500, {"error": {"message": f"upstream rejected {secret}"}})]
            ),
            collected_at="2026-08-09T13:00:00+08:00",
        )
        self.assertFalse(failed["complete"])
        self.assertNotIn(secret, failed["error"])

        previous_channel = {
            "channel_id": 45,
            "source": "rolldek",
            "complete": True,
            "collected_at": "2026-08-09T12:00:00+08:00",
            "catalog_count": 13,
            "catalog_sha256": "trusted-old",
            "relevant_models": ["seedance-2.0-720p"],
        }
        merged = merge_complete_snapshot(
            {"schema_version": 1, "channels": [previous_channel]},
            [failed],
            collected_at="2026-08-09T13:00:00+08:00",
            policy_revision="2026-08-09.1",
        )
        self.assertEqual(merged["snapshot"]["channels"], [previous_channel])
        self.assertEqual(merged["run"]["failed_channels"], [45])

    def test_mapping_report_keeps_multi_upstream_routes_separate_and_quarantines_ambiguity(self):
        snapshot = {
            "channels": [
                {
                    "channel_id": 42,
                    "source": "paisio",
                    "relevant_models": [
                        "seedance2.0-selfsur-720p",
                        "seedance-2.5-720p",
                    ],
                },
                {
                    "channel_id": 45,
                    "source": "rolldek",
                    "relevant_models": [
                        "seedance-2.0-720p",
                        "seedance-2.0-431-720p",
                    ],
                },
            ]
        }
        report = build_mapping_report(snapshot, repository_policy())
        self.assertEqual(len(report["matched"]), 2)
        self.assertEqual(
            {(row["source"], row["raw_model"]) for row in report["matched"]},
            {
                ("paisio", "seedance2.0-selfsur-720p"),
                ("rolldek", "seedance-2.0-720p"),
            },
        )
        self.assertEqual(len(report["review_required"]), 2)
        rolldek_review = next(
            row
            for row in report["review_required"]
            if row["raw_model"] == "seedance-2.0-431-720p"
        )
        self.assertEqual(
            rolldek_review["suggested_mapping"],
            {
                "stable_model": "seedance-2.0",
                "resolution": "720p",
                "confidence": "low",
                "requires_review": True,
                "reason": "family, variant, and resolution parsed but extra markers require review",
            },
        )
        self.assertEqual(report["policy_revision"], "2026-08-09.1")

    def test_route_gate_requires_current_enabled_configured_and_healthy_model(self):
        report = {
            "matched": [
                {
                    "channel_id": 42,
                    "source": "paisio",
                    "raw_model": "seedance2.0-selfsur-720p",
                    "status": "matched",
                    "stable_model": "seedance-2.0",
                    "resolution": "720p",
                },
                {
                    "channel_id": 44,
                    "source": "paisio",
                    "raw_model": "sd2-720p",
                    "status": "matched",
                    "stable_model": "seedance-2.0",
                    "resolution": "720p",
                },
                {
                    "channel_id": 45,
                    "source": "rolldek",
                    "raw_model": "seedance-2.0-720p",
                    "status": "matched",
                    "stable_model": "seedance-2.0",
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
                    "scan_status": "ok",
                    "availability": {"status": "ok"},
                    "configured_models": ["seedance2.0-selfsur-720p"],
                    "models": {"seedance2.0-selfsur-720p": {"available": True}},
                },
                {
                    "channel_id": 44,
                    "status": 1,
                    "scan_status": "ok",
                    "availability": {"status": "ok"},
                    "configured_models": ["gpt-image-2"],
                    "models": {"sd2-720p": {"available": True}},
                },
                {
                    "channel_id": 45,
                    "status": 1,
                    "scan_status": "failed",
                    "availability": {"status": "failed"},
                    "configured_models": ["seedance-2.0-720p"],
                    "models": {"seedance-2.0-720p": {"available": True}},
                },
            ],
        }
        routes = build_route_gates(report, audit, expected_day="2026-08-08")
        self.assertEqual(
            routes,
            [
                {
                    "channel_id": 42,
                    "source": "paisio",
                    "raw_model": "seedance2.0-selfsur-720p",
                    "enabled": True,
                    "healthy": True,
                }
            ],
        )

    def test_upstream_cost_evidence_keeps_exact_raw_models_and_units(self):
        ledger = {
            "days": {
                "2026-08-07": {
                    "paisio": {
                        "actual_log_complete": True,
                        "collection_status": "complete",
                        "last_attempt_status": "complete",
                        "per_model_real_cost": {
                            "sd2-720p": {
                                "kind": "video",
                                "billing_unit": "second",
                                "cost_cny_per_second": 0.31,
                            }
                        },
                    }
                },
                "2026-08-08": {
                    "paisio": {
                        "actual_log_complete": True,
                        "collection_status": "complete",
                        "last_attempt_status": "complete",
                        "per_model_real_cost": {
                            "sd2-720p": {
                                "kind": "video",
                                "billing_unit": "second",
                                "cost_cny_per_second": 0.29,
                            },
                            "sd2-pro-720p": {
                                "kind": "video",
                                "billing_unit": "call",
                                "cost_cny_per_call": 0.20,
                            },
                        },
                    },
                    "rolldek": {
                        "actual_log_complete": True,
                        "collection_status": "complete",
                        "last_attempt_status": "complete",
                        "per_model_real_cost": {},
                        "pricing_metadata": {
                            "status": "complete",
                            "models": [
                                {
                                    "model_name": "seedance-2.0-720p",
                                    "billing_mode": "per_call",
                                    "model_price": 2.5,
                                }
                            ],
                        },
                    },
                },
            }
        }
        prices = build_trusted_price_evidence(
            ledger,
            repository_policy(),
            target_day="2026-08-08",
        )
        by_route = {(row["source"], row["raw_model"]): row for row in prices}
        self.assertEqual(len(by_route), 3)
        sd2 = by_route[("paisio", "sd2-720p")]
        self.assertEqual(sd2["billing_unit"], "second")
        self.assertEqual(sd2["unit_cost_cny"], 0.29)
        self.assertEqual(sd2["version"], "actual:2026-08-08")
        self.assertEqual(sd2["evidence_type"], "actual_deduction")
        self.assertEqual(
            by_route[("paisio", "sd2-pro-720p")]["unit_cost_cny"], 0.20
        )
        rolldek = by_route[("rolldek", "seedance-2.0-720p")]
        self.assertEqual(rolldek["billing_unit"], "call")
        self.assertEqual(rolldek["unit_cost_cny"], 2.5)
        self.assertEqual(rolldek["evidence_type"], "authenticated_catalog")


if __name__ == "__main__":
    unittest.main()
