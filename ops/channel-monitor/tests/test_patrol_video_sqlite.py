import contextlib
import importlib.util
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock


PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "patrol_repair.py"
SPEC = importlib.util.spec_from_file_location("patrol_video_sqlite", PATH)
patrol = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = patrol
SPEC.loader.exec_module(patrol)


class VideoSqlitePatrolTests(unittest.TestCase):
    def fixture(self, root):
        path = pathlib.Path(root) / "video#state.db"
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(
                "create table video_jobs(billing_status text, created_at integer);"
                "create table video_webhook_outbox(status text);"
            )
        return path

    def evaluate(self, path, query):
        return patrol.PatrolChecks(patrol.CommandRunner()).evaluate(
            {"id": "video.check", "kind": "video_sqlite", "path": str(path),
             "query": query, "max_age_seconds": 1800,
             "repair_action": "restart.video_gateway"},
            10_000,
        )

    def test_read_only_queries_report_actual_settlement_and_webhook_backlog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            baseline = path.read_bytes()
            for query in ("settlement_pending", "webhook_backlog"):
                with self.subTest(query=query):
                    result = self.evaluate(path, query)
                    self.assertEqual((result.status, result.code), ("healthy", "ok"))
                    self.assertEqual(result.evidence["count"], 0)
            self.assertEqual(path.read_bytes(), baseline)
            with contextlib.closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("insert into video_jobs values('settlement_pending', 100)")
                connection.execute("insert into video_jobs values('settled', 50)")
                connection.execute("insert into video_webhook_outbox values('pending')")
                connection.execute("insert into video_webhook_outbox values('delivered')")
                connection.execute("insert into video_webhook_outbox values('dead')")
            result = self.evaluate(path, "settlement_pending")
            self.assertEqual((result.status, result.code), ("failed", "settlement_stalled"))
            self.assertEqual(result.evidence, {"count": 1, "age_seconds": 9900, "reader": "host_sqlite"})
            result = self.evaluate(path, "webhook_backlog")
            self.assertEqual((result.status, result.code), ("failed", "webhook_backlog"))
            self.assertEqual(result.evidence["count"], 1)

    def test_missing_database_is_unknown_and_never_created_or_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "missing.db"
            result = self.evaluate(path, "settlement_pending")
            self.assertEqual((result.status, result.code), ("unknown", "video_database_unavailable"))
            self.assertIsNone(result.repair_action)
            self.assertFalse(path.exists())

    def test_unknown_query_does_not_silently_check_another_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            result = self.evaluate(path, "settlement-pending")
            self.assertEqual((result.status, result.code), ("unknown", "video_query_not_allowed"))
            self.assertIsNone(result.repair_action)

    def test_incomplete_schema_is_unknown_without_restart_or_fake_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "empty.db"
            with contextlib.closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("create table video_jobs(status text, created_at integer)")
            for query in ("settlement_pending", "webhook_backlog"):
                with self.subTest(query=query):
                    result = self.evaluate(path, query)
                    self.assertEqual((result.status, result.code), ("unknown", "video_database_schema_invalid"))
                    self.assertNotIn("count", result.evidence)
                    self.assertIsNone(result.repair_action)

    def test_pending_task_with_invalid_timestamp_is_not_reported_healthy(self):
        for created_at in (0, "unknown", 20_000):
            with self.subTest(created_at=created_at), tempfile.TemporaryDirectory() as directory:
                path = self.fixture(directory)
                with contextlib.closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("insert into video_jobs values('settlement_pending', ?)", (created_at,))
                result = self.evaluate(path, "settlement_pending")
                self.assertEqual((result.status, result.code), ("unknown", "video_database_state_invalid"))
                self.assertIsNone(result.repair_action)

    def test_invalid_pending_timestamp_is_not_hidden_by_another_valid_task(self):
        for created_at in (0, None, "unknown", 20_000):
            with self.subTest(created_at=created_at), tempfile.TemporaryDirectory() as directory:
                path = self.fixture(directory)
                with contextlib.closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("insert into video_jobs values('settlement_pending', 9900)")
                    connection.execute("insert into video_jobs values('settlement_pending', ?)", (created_at,))
                result = self.evaluate(path, "settlement_pending")
                self.assertEqual((result.status, result.code), ("unknown", "video_database_state_invalid"))
                self.assertIsNone(result.repair_action)

    def owner_fallback(self, path, *, payload, query="settlement_pending", readonly=True,
                       mapped=True, source_matches=True, owner_exit=0):
        failure = sqlite3.OperationalError("private-sqlite-detail")
        failure.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
        container = "xtai-video-job-gateway-v2-production"
        mounts = [{"Type": "bind", "Source": str(path.parent) if source_matches else "/other",
                   "Destination": "/data"}]
        runner = mock.Mock()
        runner.command.side_effect = [
            types.SimpleNamespace(returncode=0, stdout=json.dumps(mounts)),
            types.SimpleNamespace(returncode=owner_exit, stdout=json.dumps(payload)),
        ]
        with (
            mock.patch.object(patrol, "VIDEO_DATABASE_OWNER_READERS", {str(path): container} if mapped else {}, create=True),
            mock.patch.object(patrol.sqlite3, "connect", side_effect=failure),
            mock.patch.object(patrol.os, "statvfs", return_value=types.SimpleNamespace(f_flag=1 if readonly else 0), create=True),
            mock.patch.object(patrol.os, "ST_RDONLY", 1, create=True),
        ):
            result = patrol.PatrolChecks(runner).evaluate(
                {"id": "video.check", "kind": "video_sqlite", "path": str(path),
                 "query": query, "max_age_seconds": 1800, "repair_action": "restart.video_gateway"}, 10_000,
            )
        return result, runner

    def test_sandbox_fallback_reads_only_the_bound_container_as_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            result, runner = self.owner_fallback(path, payload={"count": 0, "oldest": 0, "invalid": 0})
            self.assertEqual((result.status, result.code), ("healthy", "ok"))
            self.assertEqual(result.evidence["reader"], "container_owner")
            args = runner.command.call_args_list[-1].args[0]
            self.assertEqual(args[:5], ("/usr/bin/docker", "exec", "--user", "10002:999", "xtai-video-job-gateway-v2-production"))
            self.assertEqual(args[-2:], ("settlement_pending", "10000"))

    def test_owner_fallback_keeps_real_stall_and_callback_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            result, _ = self.owner_fallback(path, payload={"count": 1, "oldest": 100, "invalid": 0})
            self.assertEqual((result.status, result.code), ("failed", "settlement_stalled"))
            result, _ = self.owner_fallback(path, payload={"count": 2}, query="webhook_backlog")
            self.assertEqual((result.status, result.code), ("failed", "webhook_backlog"))

    def test_owner_failure_or_malformed_statistics_never_become_healthy_zero(self):
        for payload, owner_exit in (({"count": 0, "oldest": 0, "invalid": 0}, 1),
                                    ({}, 0), ({"count": False}, 0),
                                    ({"error": "video_database_schema_invalid"}, 0),
                                    ({"count": 2, "oldest": 9900, "invalid": 1}, 0)):
            with self.subTest(payload=payload, owner_exit=owner_exit), tempfile.TemporaryDirectory() as directory:
                path = self.fixture(directory)
                result, _ = self.owner_fallback(path, payload=payload, owner_exit=owner_exit)
                self.assertEqual(result.status, "unknown")
                self.assertNotIn("count", result.evidence)
                self.assertIsNone(result.repair_action)
                self.assertNotIn("private-sqlite-detail", json.dumps(result.to_dict()))

    def test_unmapped_or_writable_database_never_invokes_container_fallback(self):
        for fields in ({"mapped": False}, {"readonly": False}):
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as directory:
                path = self.fixture(directory)
                result, runner = self.owner_fallback(path, payload={}, **fields)
                self.assertEqual(result.status, "unknown")
                runner.command.assert_not_called()

    def test_owner_fallback_refuses_a_different_container_data_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            result, runner = self.owner_fallback(path, payload={}, source_matches=False)
            self.assertEqual(result.status, "unknown")
            self.assertEqual(runner.command.call_count, 1)
            self.assertIsNone(result.repair_action)

    def test_fixed_owner_program_reads_actual_fixture_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            before = path.read_bytes()
            for query, expected in (("settlement_pending", {"count": 0, "oldest": 0, "invalid": 0}),
                                    ("webhook_backlog", {"count": 0})):
                output = io.StringIO()
                with (mock.patch.object(pathlib, "Path", return_value=path),
                      mock.patch.object(sys, "argv", ["owner", query, "10000"]),
                      contextlib.redirect_stdout(output)):
                    exec(compile(patrol.OWNER_VIDEO_SQLITE_READER, "owner-reader", "exec"), {})
                self.assertEqual(json.loads(output.getvalue()), expected)
            self.assertEqual(path.read_bytes(), before)

    def test_fixed_owner_program_never_prints_corrupt_timestamp_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            with contextlib.closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("insert into video_jobs values('settlement_pending', ?)", ("private-invalid-timestamp",))
            output = io.StringIO()
            with (mock.patch.object(pathlib, "Path", return_value=path),
                  mock.patch.object(sys, "argv", ["owner", "settlement_pending", "10000"]),
                  contextlib.redirect_stdout(output)):
                exec(compile(patrol.OWNER_VIDEO_SQLITE_READER, "owner-reader", "exec"), {})
            self.assertEqual(json.loads(output.getvalue()), {"error": "video_database_state_invalid"})
            self.assertNotIn("private-invalid-timestamp", output.getvalue())


if __name__ == "__main__":
    unittest.main()
