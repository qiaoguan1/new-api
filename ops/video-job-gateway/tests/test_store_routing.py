import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import Store


class StoreRoutingTests(unittest.TestCase):
    def finish_provider_failure(self, store, index, error, *, provider="paisio"):
        snapshot, _ = store.create(
            request_id=f"provider-failure-{provider}-{index}",
            fingerprint=f"provider-failure-fingerprint-{provider}-{index}",
            protocol_version="xtai-video-billing-v2.1",
            catalog_revision="test",
            stable_model="seedance-2.0",
            provider_id=provider,
            upstream_model="sd2-480p",
            adapter_revision=f"{provider}-v1",
            payload_json="{}",
        )
        job_id = str(snapshot["job_id"])
        self.assertIsNotNone(store.claim_submit(job_id))
        store.mark_running(job_id, f"upstream-task-{index}", "queued", 5)
        store.finish(
            job_id,
            "failed",
            error=error,
            upstream_task_id=f"upstream-task-{index}",
            upstream_status="failed",
        )

    def test_existing_database_receives_additive_route_columns(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = pathlib.Path(directory) / "video-jobs.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                create table video_jobs (
                    job_id text primary key, request_id text not null unique,
                    fingerprint text not null, protocol_version text not null,
                    catalog_revision text not null, stable_model text not null,
                    provider_id text not null, upstream_model text not null,
                    adapter_revision text not null, status text not null,
                    payload_json text not null, result_json text not null default '',
                    error_json text not null default '', upstream_task_id text not null default '',
                    upstream_status text not null default '', submit_attempts integer not null default 0,
                    poll_attempts integer not null default 0, poll_errors integer not null default 0,
                    missing_count integer not null default 0, missing_last_at integer not null default 0,
                    next_poll_at integer not null default 0, created_at integer not null,
                    updated_at integer not null, finished_at integer not null default 0
                )
                """
            )
            connection.commit()
            connection.close()

            store = Store(pathlib.Path(directory))
            check = store.connect()
            columns = {row[1] for row in check.execute("pragma table_info(video_jobs)")}
            tables = {
                row[0]
                for row in check.execute(
                    "select name from sqlite_master where type='table'"
                )
            }
            check.close()
            self.assertTrue(
                {"route_plan_json", "route_index", "selection_reason", "route_history_json"}.issubset(columns)
            )
            self.assertIn("video_provider_generation_quarantines", tables)

    def test_route_plan_is_persisted_and_advances_only_before_task_creation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            route_plan = [
                {
                    "provider_id": "toonflow",
                    "upstream_model": "Seedance 2.0",
                    "adapter_revision": "toonflow-v1",
                    "send_resolution": True,
                },
                {
                    "provider_id": "paisio",
                    "upstream_model": "sd2-720p",
                    "adapter_revision": "paisio-v1",
                    "send_resolution": False,
                },
            ]
            snapshot, reused = store.create(
                request_id="request-1",
                fingerprint="fingerprint",
                protocol_version="xtai-relay-v1",
                catalog_revision="test",
                stable_model="seedance-2.0",
                provider_id="toonflow",
                upstream_model="Seedance 2.0",
                adapter_revision="toonflow-v1",
                payload_json="{}",
                route_plan=route_plan,
                selection_reason="deterministic_equal_priority",
            )
            self.assertFalse(reused)
            self.assertNotIn("provider_id", snapshot)
            self.assertNotIn("route_plan", snapshot)

            reused, was_reused = store.create(
                request_id="request-1",
                fingerprint="fingerprint",
                protocol_version="xtai-relay-v1",
                catalog_revision="changed",
                stable_model="seedance-2.0",
                provider_id="paisio",
                upstream_model="different",
                adapter_revision="different",
                payload_json="{}",
            )
            self.assertTrue(was_reused)
            self.assertEqual(reused["job_id"], snapshot["job_id"])

            job_id = str(snapshot["job_id"])
            claimed = store.claim_submit(job_id)
            self.assertEqual(claimed["provider_id"], "toonflow")
            advanced = store.advance_route(
                job_id,
                error={"code": "definitive_rejection", "uncertain": False},
            )
            self.assertTrue(advanced)
            next_claim = store.claim_submit(job_id)
            self.assertEqual(next_claim["provider_id"], "paisio")
            self.assertEqual(next_claim["upstream_model"], "sd2-720p")
            self.assertEqual(next_claim["route_index"], 1)
            self.assertEqual(len(json.loads(next_claim["route_history_json"])), 1)

            store.mark_running(job_id, "upstream-123", "queued", 5)
            self.assertFalse(
                store.advance_route(
                    job_id,
                    error={"code": "must_not_replay", "uncertain": False},
                )
            )

    def test_three_recent_definite_failures_exclude_only_that_provider(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            for index in range(3):
                snapshot, _ = store.create(
                    request_id=f"health-{index}",
                    fingerprint=f"fingerprint-{index}",
                    protocol_version="xtai-relay-v1",
                    catalog_revision="test",
                    stable_model="seedance-2.0",
                    provider_id="toonflow",
                    upstream_model="Seedance 2.0",
                    adapter_revision="toonflow-v1",
                    payload_json="{}",
                    route_plan=[
                        {"provider_id": "toonflow", "upstream_model": "Seedance 2.0", "adapter_revision": "toonflow-v1"},
                        {"provider_id": "paisio", "upstream_model": "sd2-720p", "adapter_revision": "paisio-v1"},
                    ],
                )
                job_id = str(snapshot["job_id"])
                store.claim_submit(job_id)
                self.assertTrue(
                    store.advance_route(
                        job_id,
                        error={"code": "definite", "uncertain": False},
                    )
                )

            self.assertEqual(store.unhealthy_providers(), {"toonflow"})

    def test_two_post_creation_credential_failures_persistently_quarantine_provider(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            data_dir = pathlib.Path(directory)
            store = Store(data_dir)
            error = {
                "code": "upstream_video_failed",
                "category": "upstream",
                "message": "refresh leased account credential: invalid Adobe refresh response",
                "uncertain": False,
            }
            self.finish_provider_failure(store, 0, error)
            self.assertEqual(store.unhealthy_providers(), set())

            self.finish_provider_failure(store, 1, error)
            self.assertEqual(store.unhealthy_providers(), {"paisio"})
            quarantine = store.generation_quarantines()["paisio"]
            self.assertEqual(quarantine["status"], "active")
            self.assertEqual(quarantine["failure_count"], 2)
            self.assertEqual(
                quarantine["reason_code"],
                "provider_credential_refresh_failed",
            )

            reopened = Store(data_dir)
            self.assertEqual(reopened.unhealthy_providers(), {"paisio"})
            self.assertTrue(reopened.clear_generation_quarantine("paisio"))
            self.assertEqual(reopened.unhealthy_providers(), set())
            self.assertEqual(
                reopened.generation_quarantines(active_only=False)["paisio"]["status"],
                "cleared",
            )

    def test_reference_fetch_rejections_do_not_quarantine_provider(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            error = {
                "code": "video_reference_fetch_rejected",
                "category": "upstream",
                "message": "Cannot fetch content from the provided URL",
                "uncertain": False,
            }
            self.finish_provider_failure(store, 0, error)
            self.finish_provider_failure(store, 1, error)
            self.assertEqual(store.unhealthy_providers(), set())
            self.assertEqual(store.generation_quarantines(), {})

    def test_aged_settlement_backlog_excludes_only_affected_provider(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            current = int(time.time())
            with store.connect() as connection:
                for provider, age, attempts, billing_status in (
                    ("paisio", 3600, 5, "settlement_pending"),
                    ("toonflow", 60, 5, "settlement_pending"),
                    ("rolldek", 3600, 5, "settled"),
                    ("lowattempts", 3600, 2, "settlement_pending"),
                ):
                    connection.execute(
                        """
                        insert into video_jobs (
                            job_id,request_id,fingerprint,protocol_version,catalog_revision,
                            stable_model,provider_id,upstream_model,adapter_revision,status,
                            payload_json,created_at,updated_at,finished_at,billing_status,
                            settlement_query_attempts,settlement_query_last_error
                        ) values (?,?,?,?,?,?,?,?,?,'succeeded','{}',?,?,?,?,?,?)
                        """,
                        (
                            f"vjob_{provider:0<32}"[:38],
                            f"request-{provider}",
                            f"fingerprint-{provider}",
                            "xtai-video-billing-v2.1",
                            "test",
                            "seedance-2.0",
                            provider,
                            "upstream-model",
                            f"{provider}-v1",
                            current - age,
                            current,
                            current - age,
                            billing_status,
                            attempts,
                            "provider_billing_record_not_ready",
                        ),
                    )
                connection.commit()

            self.assertEqual(
                store.unhealthy_settlement_providers(
                    min_age_seconds=1800,
                    min_attempts=3,
                    now=current,
                ),
                {"paisio"},
            )


if __name__ == "__main__":
    unittest.main()
