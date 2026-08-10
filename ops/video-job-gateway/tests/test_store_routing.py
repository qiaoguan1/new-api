import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import Store


class StoreRoutingTests(unittest.TestCase):
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
            check.close()
            self.assertTrue(
                {"route_plan_json", "route_index", "selection_reason", "route_history_json"}.issubset(columns)
            )

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


if __name__ == "__main__":
    unittest.main()
