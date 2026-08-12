import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "patrol_repair.py"
SPEC = importlib.util.spec_from_file_location("patrol_repair", MODULE_PATH)
patrol = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = patrol
SPEC.loader.exec_module(patrol)


BEIJING = ZoneInfo("Asia/Shanghai")


class PolicyTests(unittest.TestCase):
    def test_default_policy_covers_the_complete_relay_chain(self):
        policy = patrol.load_policy(pathlib.Path(__file__).resolve().parents[1] / "config" / "patrol-repair-policy.json")
        check_ids = {item["id"] for item in policy["checks"]}
        self.assertTrue({
            "systemd.docker", "systemd.admin", "systemd.regenerate_path",
            "docker.new_api", "docker.nginx", "docker.postgres", "docker.redis",
            "docker.video_gateway", "http.new_api", "disk.root", "backup.daily",
            "artifact.upstream_ledger", "artifact.daily_audit", "artifact.generic_pricing",
            "artifact.video_pricing", "artifact.balance_live", "video.settlement_pending",
            "video.webhook_backlog",
        }.issubset(check_ids))

    def test_policy_rejects_unknown_or_forbidden_repair_action(self):
        with self.assertRaisesRegex(patrol.PolicyError, "repair_action_not_allowed"):
            patrol.validate_policy({
                "schema_version": 1,
                "max_actions_per_run": 1,
                "repair_cooldown_seconds": 3600,
                "incident_reminder_seconds": 3600,
                "checks": [{"id": "x", "kind": "artifact", "repair_action": "shell.anything"}],
            })

    def test_expected_business_day_waits_for_morning_jobs(self):
        before = datetime.datetime(2026, 8, 13, 8, 59, tzinfo=BEIJING)
        after = datetime.datetime(2026, 8, 13, 9, 5, tzinfo=BEIJING)
        self.assertEqual(patrol.expected_business_day(before), "2026-08-11")
        self.assertEqual(patrol.expected_business_day(after), "2026-08-12")


class RepairCoordinatorTests(unittest.TestCase):
    def result(self, status="failed", action="restart.new_api", check_id="docker.new_api"):
        return patrol.CheckResult(
            check_id=check_id,
            status=status,
            severity="critical",
            code="not_running",
            repair_action=action,
            evidence={"state": "stopped"},
        )

    def test_allowlisted_repair_runs_once_and_is_post_checked(self):
        runner = mock.Mock()
        check = mock.Mock(return_value=self.result(status="healthy", action=None))
        coordinator = patrol.RepairCoordinator(runner, {"max_actions_per_run": 2, "repair_cooldown_seconds": 3600})

        final, actions, state = coordinator.repair([self.result()], {"actions": {}}, check, now=1000)

        runner.run_action.assert_called_once_with("restart.new_api")
        self.assertEqual(final[0].status, "healthy")
        self.assertEqual(actions[0]["status"], "repaired")
        self.assertEqual(state["actions"]["restart.new_api"]["last_attempt_at"], 1000)

    def test_forbidden_high_risk_fault_is_never_executed(self):
        runner = mock.Mock()
        result = self.result(action=None, check_id="artifact.generic_pricing")
        coordinator = patrol.RepairCoordinator(runner, {"max_actions_per_run": 2, "repair_cooldown_seconds": 3600})

        final, actions, _ = coordinator.repair([result], {"actions": {}}, mock.Mock(), now=1000)

        runner.run_action.assert_not_called()
        self.assertEqual(final[0].status, "failed")
        self.assertEqual(actions, [])

    def test_action_budget_and_persistent_cooldown_prevent_repair_storm(self):
        runner = mock.Mock()
        first = self.result(action="restart.new_api", check_id="docker.new_api")
        second = self.result(action="restart.nginx", check_id="docker.nginx")
        coordinator = patrol.RepairCoordinator(runner, {"max_actions_per_run": 1, "repair_cooldown_seconds": 3600})
        state = {"actions": {"restart.new_api": {"last_attempt_at": 900}}}

        final, actions, _ = coordinator.repair([first, second], state, mock.Mock(), now=1000)

        runner.run_action.assert_not_called()
        self.assertEqual(actions, [])
        self.assertEqual([row.code for row in final], ["repair_cooldown", "repair_budget_exhausted"])


class IncidentLifecycleTests(unittest.TestCase):
    def test_open_reminder_and_recovery_are_deduplicated(self):
        failed = patrol.CheckResult("docker.new_api", "failed", "critical", "not_running", None, {})
        healthy = patrol.CheckResult("docker.new_api", "healthy", "info", "ok", None, {})

        events, state = patrol.observe_incidents([failed], {"incidents": {}}, now=100, reminder_seconds=3600)
        self.assertEqual([event.kind for event in events], ["patrol_incident_open"])
        state = patrol.record_deliveries(state, events, now=100)
        events, state = patrol.observe_incidents([failed], state, now=200, reminder_seconds=3600)
        self.assertEqual(events, [])
        events, state = patrol.observe_incidents([failed], state, now=3800, reminder_seconds=3600)
        self.assertEqual([event.kind for event in events], ["patrol_incident_reminder"])
        state = patrol.record_deliveries(state, events, now=3800)
        events, state = patrol.observe_incidents([healthy], state, now=3900, reminder_seconds=3600)
        self.assertEqual([event.kind for event in events], ["patrol_incident_recovered"])
        events, state = patrol.observe_incidents([healthy], state, now=4000, reminder_seconds=3600)
        self.assertEqual([event.kind for event in events], ["patrol_incident_recovered"])
        state = patrol.record_deliveries(state, events, now=4000)
        events, _ = patrol.observe_incidents([healthy], state, now=4100, reminder_seconds=3600)
        self.assertEqual(events, [])

    def test_notification_payload_and_report_redact_secrets(self):
        event = patrol.IncidentEvent(
            kind="patrol_incident_open",
            check_id="http.new_api",
            severity="critical",
            code="Bearer abc password=hello https://u:p@example.test/?token=secret",
            occurred_at=100,
        )
        payload = patrol.notification_payload(event)
        serialized = json.dumps(payload)
        for forbidden in ("abc", "hello", "secret", "u:p"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(payload["code"], "redacted")

    def test_private_json_is_atomic_and_mode_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "state.json"
            patrol.write_private_json(target, {"ok": True})
            self.assertEqual(json.loads(target.read_text()), {"ok": True})
            if os.name == "posix":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class CheckImplementationTests(unittest.TestCase):
    def test_backup_requires_a_fresh_verified_sha256_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = pathlib.Path(directory) / "20260813"
            snapshot.mkdir()
            dump = snapshot / "database.dump"
            dump.write_bytes(b"verified")
            digest = hashlib.sha256(dump.read_bytes()).hexdigest()
            manifest = snapshot / "SHA256SUMS"
            manifest.write_text(f"{digest}  database.dump\n", encoding="ascii")
            item = {
                "id": "backup.daily", "kind": "backup", "root": directory,
                "glob": "*/SHA256SUMS", "max_age_seconds": 3600, "severity": "critical",
            }
            result = patrol.PatrolChecks(mock.Mock()).evaluate(item, int(manifest.stat().st_mtime) + 10)
            self.assertEqual(result.status, "healthy")
            dump.write_bytes(b"tampered")
            result = patrol.PatrolChecks(mock.Mock()).evaluate(item, int(manifest.stat().st_mtime) + 10)
            self.assertEqual((result.status, result.code), ("failed", "backup_stale_or_invalid"))

    def test_docker_check_accepts_running_container_without_healthcheck(self):
        runner = mock.Mock()
        runner.command.return_value = mock.Mock(returncode=0, stdout='{"Running":true,"Health":null}')
        item = {"id": "docker.nginx", "kind": "docker", "target": "nginx", "severity": "critical"}
        result = patrol.PatrolChecks(runner).evaluate(item, 100)
        self.assertEqual((result.status, result.code), ("healthy", "ok"))

    def test_failed_pricing_artifact_has_no_repair_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "pricing.json"
            path.write_text(json.dumps({"runs": [{"date": "2026-08-12", "status": "failed", "error": "private"}]}))
            item = {
                "id": "artifact.generic_pricing", "kind": "artifact", "path": str(path),
                "artifact_type": "generic_pricing", "severity": "critical",
            }
            now = int(datetime.datetime(2026, 8, 13, 10, tzinfo=BEIJING).timestamp())
            result = patrol.PatrolChecks(mock.Mock()).evaluate(item, now)
            self.assertEqual((result.status, result.code, result.repair_action), ("failed", "scheduled_run_failed", None))

    def test_notification_config_rejects_public_endpoint(self):
        with self.assertRaisesRegex(patrol.PatrolError, "notification_url_invalid"):
            patrol._notify_config({"UPSTREAM_BALANCE_ALERT_NOTIFY_URL": "https://public.example.test"})


if __name__ == "__main__":
    unittest.main()
