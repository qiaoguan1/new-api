import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from video_consumption import (  # noqa: E402
    build_monitor_snapshots,
    dedupe_provider_usage,
    gateway_rows_from_sqlite,
    parse_toonflow_operation_rows,
    parse_newapi_video_task_rows,
    reconcile_video_usage,
)


DAY = "2026-08-09"


class ToonflowParsingTests(unittest.TestCase):
    def test_completed_cost_and_beijing_day_are_preserved(self):
        payload = {
            "data": {
                "list": [
                    {
                        "taskICode": "tf-1",
                        "modelName": "seedance-2.0-full-720p",
                        "state": 2,
                        "price": "1.25",
                        "creationTime": "2026-08-09T23:59:59+08:00",
                        "completionTime": "2026-08-10T00:01:00+08:00",
                    },
                    {
                        "taskICode": "tf-2",
                        "modelName": "seedance-2.0-fast-720p",
                        "state": -1,
                        "price": "9.99",
                        "creationTime": "2026-08-09 08:00:00",
                    },
                    {
                        "taskICode": "tf-next-day",
                        "modelName": "seedance-2.0-full-720p",
                        "state": 2,
                        "price": "3",
                        "creationTime": "2026-08-10 00:00:00",
                    },
                ]
            }
        }

        rows = parse_toonflow_operation_rows(payload, DAY, rate=1.0, fetched_at=1786323600)

        by_task = {row["provider_task_id"]: row for row in rows}
        self.assertEqual(set(by_task), {"tf-1", "tf-2"})
        self.assertEqual(by_task["tf-1"]["state"], "completed")
        self.assertEqual(by_task["tf-1"]["actual_cost_cny"], 1.25)
        self.assertEqual(by_task["tf-1"]["actual_cost_status"], "actual")
        self.assertEqual(by_task["tf-2"]["state"], "failed")
        self.assertEqual(by_task["tf-2"]["actual_cost_cny"], 0.0)
        self.assertEqual(by_task["tf-2"]["actual_cost_status"], "zero_verified")

    def test_duplicate_task_uses_latest_terminal_evidence_once(self):
        rows = [
            {
                "provider_id": "toonflow",
                "provider_task_id": "same",
                "state": "running",
                "actual_cost_cny": None,
                "completed_at_epoch": 0,
                "created_at_epoch": 100,
            },
            {
                "provider_id": "toonflow",
                "provider_task_id": "same",
                "state": "completed",
                "actual_cost_cny": 2.0,
                "completed_at_epoch": 200,
                "created_at_epoch": 100,
            },
        ]

        result = dedupe_provider_usage(rows)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["state"], "completed")
        self.assertEqual(result[0]["actual_cost_cny"], 2.0)


class PaisioTaskParsingTests(unittest.TestCase):
    def test_authenticated_video_tasks_use_task_id_and_quota(self):
        payload = {
            "data": {
                "items": [
                    {
                        "task_id": "paisio-1",
                        "action": "videoGenerate",
                        "status": "SUCCESS",
                        "quota": 625000,
                        "created_at": 1786291199,
                        "finish_time": 1786291201,
                        "result_url": "must-not-persist",
                    },
                    {
                        "task_id": "paisio-2",
                        "action": "videoGenerate",
                        "status": "FAILURE",
                        "quota": 625000,
                        "created_at": 1786291100,
                        "fail_reason": "internal detail",
                    },
                    {
                        "task_id": "not-video",
                        "action": "imageGenerate",
                        "status": "SUCCESS",
                        "quota": 100,
                        "created_at": 1786291100,
                    },
                ]
            }
        }

        rows = parse_newapi_video_task_rows(payload, DAY, provider_id="paisio", rate=1.0)

        by_task = {row["provider_task_id"]: row for row in rows}
        self.assertEqual(set(by_task), {"paisio-1", "paisio-2"})
        self.assertEqual(by_task["paisio-1"]["actual_cost_cny"], 1.25)
        self.assertEqual(by_task["paisio-1"]["actual_cost_status"], "actual")
        self.assertEqual(by_task["paisio-2"]["actual_cost_cny"], 0.0)
        self.assertEqual(by_task["paisio-2"]["actual_cost_status"], "zero_verified")
        self.assertNotIn("result_url", by_task["paisio-1"])
        self.assertNotIn("fail_reason", by_task["paisio-2"])


class GatewayReaderTests(unittest.TestCase):
    def test_reads_prior_beijing_day_and_separates_sale_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "jobs.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """create table video_jobs(
                job_id text, provider_id text, upstream_task_id text, stable_model text,
                status text, payload_json text, created_at integer, updated_at integer,
                finished_at integer)"""
            )
            payload = {
                "resolution": "720p",
                "_relay_price": {"amount_cny_exact": "4.50", "price_source": "official_1_5_fallback"},
            }
            connection.execute(
                "insert into video_jobs values(?,?,?,?,?,?,?,?,?)",
                (
                    "job-1",
                    "toonflow",
                    "tf-1",
                    "seedance-2.0-full",
                    "succeeded",
                    json.dumps(payload),
                    1786291199,
                    1786291200,
                    1786291201,
                ),
            )
            connection.execute(
                "insert into video_jobs values(?,?,?,?,?,?,?,?,?)",
                (
                    "job-next",
                    "toonflow",
                    "tf-next",
                    "seedance-2.0-full",
                    "succeeded",
                    json.dumps(payload),
                    1786291200,
                    1786291200,
                    1786291201,
                ),
            )
            connection.commit()
            connection.close()

            rows = gateway_rows_from_sqlite(path, DAY)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relay_job_id"], "job-1")
        self.assertEqual(rows[0]["relay_sale_cny"], 4.5)
        self.assertEqual(rows[0]["resolution"], "720p")


class ReconciliationTests(unittest.TestCase):
    def test_exact_task_match_wins_and_failed_cost_is_zero(self):
        jobs = [
            {
                "relay_job_id": "job-1",
                "provider_id": "toonflow",
                "upstream_task_id": "tf-1",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "status": "succeeded",
                "created_at_epoch": 100,
                "relay_sale_cny": 4.5,
            },
            {
                "relay_job_id": "job-2",
                "provider_id": "toonflow",
                "upstream_task_id": "tf-2",
                "stable_model": "seedance-2.0-fast",
                "resolution": "720p",
                "status": "failed",
                "created_at_epoch": 200,
                "relay_sale_cny": 3.0,
            },
        ]
        provider = [
            {
                "provider_id": "toonflow",
                "provider_task_id": "tf-1",
                "raw_model": "full",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "state": "completed",
                "created_at_epoch": 105,
                "actual_cost_cny": 1.25,
                "actual_cost_status": "actual",
                "evidence_source": "toonflow_web_operation_log",
                "fetched_at": 300,
            },
            {
                "provider_id": "toonflow",
                "provider_task_id": "tf-2",
                "raw_model": "fast",
                "stable_model": "seedance-2.0-fast",
                "resolution": "720p",
                "state": "failed",
                "created_at_epoch": 205,
                "actual_cost_cny": 0.0,
                "actual_cost_status": "zero_verified",
                "evidence_source": "toonflow_web_operation_log",
                "fetched_at": 300,
            },
        ]

        rows = reconcile_video_usage(jobs, provider)

        self.assertEqual([row["match_status"] for row in rows], ["exact", "exact"])
        self.assertEqual(rows[0]["upstream_actual_cost_cny"], 1.25)
        self.assertEqual(rows[1]["upstream_actual_cost_cny"], 0.0)
        self.assertEqual(sum(row["upstream_actual_cost_cny"] or 0 for row in rows), 1.25)

    def test_ambiguous_time_matches_remain_unknown(self):
        jobs = [
            {
                "relay_job_id": "job-1",
                "provider_id": "paisio",
                "upstream_task_id": "",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "status": "succeeded",
                "created_at_epoch": 100,
                "relay_sale_cny": 4.5,
            }
        ]
        provider = [
            {
                "provider_id": "paisio",
                "provider_task_id": "p-1",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "state": "completed",
                "created_at_epoch": 101,
                "actual_cost_cny": 1.0,
            },
            {
                "provider_id": "paisio",
                "provider_task_id": "p-2",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "state": "completed",
                "created_at_epoch": 102,
                "actual_cost_cny": 1.0,
            },
        ]

        rows = reconcile_video_usage(jobs, provider, match_window_seconds=60)

        self.assertEqual(rows[0]["match_status"], "ambiguous")
        self.assertEqual(rows[0]["actual_cost_status"], "unknown")
        self.assertIsNone(rows[0]["upstream_actual_cost_cny"])


class MonitorProjectionTests(unittest.TestCase):
    def test_private_provider_metrics_and_public_redaction(self):
        reconciled = [
            {
                "relay_job_id": "job-1",
                "provider_id": "toonflow",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "status": "succeeded",
                "match_status": "exact",
                "actual_cost_status": "actual",
                "upstream_actual_cost_cny": 1.25,
                "relay_sale_cny": 4.5,
                "fetched_at": 300,
            },
            {
                "relay_job_id": "job-2",
                "provider_id": "paisio",
                "stable_model": "seedance-2.0-full",
                "resolution": "720p",
                "status": "failed",
                "match_status": "unmatched",
                "actual_cost_status": "unknown",
                "upstream_actual_cost_cny": None,
                "relay_sale_cny": 4.5,
                "fetched_at": None,
            },
        ]

        snapshots = build_monitor_snapshots(DAY, reconciled, generated_at="2026-08-10T08:20:00+08:00")

        private = snapshots["private"]
        public = snapshots["public"]
        providers = {row["provider_id"]: row for row in private["providers"]}
        self.assertEqual(providers["toonflow"]["actual_cost_coverage"], 1.0)
        self.assertEqual(providers["paisio"]["actual_cost_coverage"], 0.0)
        self.assertEqual(public["models"][0]["task_count"], 2)
        self.assertEqual(public["models"][0]["success_rate"], 0.5)
        serialized = json.dumps(public, ensure_ascii=False).lower()
        for forbidden in (
            "toonflow",
            "paisio",
            "provider",
            "channel",
            "cost",
            "price",
            "sale",
            "margin",
            "token",
            "credential",
            "username",
            "password",
            "upstream",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
