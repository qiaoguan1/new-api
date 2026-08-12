#!/usr/bin/env python3
"""Deterministic health checks and bounded self-healing for the XingTu relay."""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parent.parent
BEIJING = ZoneInfo("Asia/Shanghai")
SAFE_IDENTIFIER = re.compile(r"^[a-z0-9_.-]{1,80}$")
VALID_SEVERITIES = {"info", "warning", "critical"}
ALLOWED_REPAIR_ACTIONS = {
    "start.docker", "restart.admin", "start.regenerate_path", "restart.new_api",
    "restart.nginx", "restart.video_gateway", "run.backup",
    "run.fetch_upstream_balance", "run.scan_daily_audit", "run.balance_monitor",
    "run.generate_monitor",
}
ALLOWED_CHECK_KINDS = {"systemd", "docker", "http", "disk", "backup", "artifact", "video_sqlite"}
MAX_JSON_BYTES = 32 * 1024 * 1024


class PolicyError(ValueError):
    """Raised when the root-owned repair policy is unsafe or malformed."""


class PatrolError(RuntimeError):
    """Raised for a sanitized patrol execution failure."""


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """One bounded health observation with no credential-bearing data."""

    check_id: str
    status: str
    severity: str
    code: str
    repair_action: str | None
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class IncidentEvent:
    """One incident lifecycle event suitable for the fixed mail template."""

    kind: str
    check_id: str
    severity: str
    code: str
    occurred_at: int


def write_private_json(path: pathlib.Path, value: Any) -> None:
    """Atomically write private state/report JSON with mode 0600."""

    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_json(path: pathlib.Path, default: Any = None, *, maximum: int = MAX_JSON_BYTES) -> Any:
    source = pathlib.Path(path)
    try:
        metadata = source.stat()
        if metadata.st_size > maximum:
            raise PatrolError("json_input_too_large")
        return json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise PatrolError("json_input_invalid") from error


def _safe_identifier(value: Any, fallback: str = "redacted") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if SAFE_IDENTIFIER.fullmatch(candidate) else fallback


def expected_business_day(now: datetime.datetime | None = None) -> str:
    """Return the latest day whose Beijing 08:20-08:45 jobs should be complete."""

    current = now.astimezone(BEIJING) if now else datetime.datetime.now(BEIJING)
    days_back = 1 if current.time() >= datetime.time(9, 5) else 2
    return (current.date() - datetime.timedelta(days=days_back)).isoformat()


def validate_policy(policy: Any) -> dict[str, Any]:
    """Validate that configuration can select only compiled checks/actions."""

    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise PolicyError("policy_schema_invalid")
    try:
        budget = int(policy.get("max_actions_per_run"))
        cooldown = int(policy.get("repair_cooldown_seconds"))
        reminder = int(policy.get("incident_reminder_seconds"))
    except (TypeError, ValueError) as error:
        raise PolicyError("policy_limits_invalid") from error
    if budget < 0 or budget > 10 or cooldown < 60 or reminder < 300:
        raise PolicyError("policy_limits_invalid")
    checks = policy.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 100:
        raise PolicyError("policy_checks_invalid")
    seen: set[str] = set()
    for item in checks:
        if not isinstance(item, dict):
            raise PolicyError("policy_check_invalid")
        check_id = _safe_identifier(item.get("id"), "")
        if not check_id or check_id in seen:
            raise PolicyError("policy_check_id_invalid")
        seen.add(check_id)
        if item.get("kind") not in ALLOWED_CHECK_KINDS:
            raise PolicyError("check_kind_not_allowed")
        if item.get("severity", "critical") not in VALID_SEVERITIES:
            raise PolicyError("check_severity_invalid")
        action = item.get("repair_action")
        if action is not None and action not in ALLOWED_REPAIR_ACTIONS:
            raise PolicyError("repair_action_not_allowed")
    validated = dict(policy)
    validated.update(max_actions_per_run=budget, repair_cooldown_seconds=cooldown, incident_reminder_seconds=reminder)
    return validated


def load_policy(path: pathlib.Path) -> dict[str, Any]:
    source = pathlib.Path(path)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise PolicyError("policy_file_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PolicyError("policy_file_unsafe")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022:
        raise PolicyError("policy_file_unsafe")
    return validate_policy(read_json(source))


class CommandRunner:
    """Execute compiled commands; configuration never supplies shell text."""

    ACTIONS: Mapping[str, tuple[tuple[str, ...], int]] = {
        "start.docker": (("/usr/bin/systemctl", "start", "docker.service"), 120),
        "restart.admin": (("/usr/bin/systemctl", "restart", "channel-monitor-admin.service"), 60),
        "start.regenerate_path": (("/usr/bin/systemctl", "start", "channel-monitor-regenerate.path"), 60),
        "restart.new_api": (("/usr/bin/docker", "restart", "ai-api-stack-new-api-1"), 120),
        "restart.nginx": (("/usr/bin/docker", "restart", "ai-api-stack-nginx-1"), 120),
        "restart.video_gateway": (("/usr/bin/docker", "restart", "xtai-video-job-gateway-v2-production"), 120),
        "run.backup": (("/usr/bin/flock", "-n", "/run/lock/newapi-daily-backup.lock", "/usr/bin/python3", "/opt/ai-api-stack/channel-monitor/scripts/newapi-daily-backup.py"), 900),
        "run.fetch_upstream_balance": (("/usr/bin/flock", "-n", "/run/lock/fetch-upstream-balance.lock", "/usr/bin/python3", "/opt/ai-api-stack/channel-monitor/scripts/fetch-upstream-balance.py"), 300),
        "run.scan_daily_audit": (("/usr/bin/flock", "-n", "/run/lock/scan-upstream-daily.lock", "/usr/bin/python3", "/opt/ai-api-stack/channel-monitor/scripts/scan-upstream-daily.py"), 300),
        "run.balance_monitor": (("/bin/bash", "-c", "set -a; . /opt/ai-api-stack/channel-monitor/balance-alert.env; set +a; exec /usr/bin/python3 /opt/ai-api-stack/channel-monitor/scripts/monitor-upstream-balances.py"), 300),
        "run.generate_monitor": (("/usr/bin/systemctl", "start", "channel-monitor-regenerate.service"), 300),
    }

    def command(self, arguments: Sequence[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(tuple(arguments), capture_output=True, text=True, check=False, timeout=timeout, stdin=subprocess.DEVNULL)

    def run_action(self, action: str) -> None:
        if action not in ALLOWED_REPAIR_ACTIONS or action not in self.ACTIONS:
            raise PatrolError("repair_action_not_allowed")
        arguments, timeout = self.ACTIONS[action]
        if self.command(arguments, timeout=timeout).returncode != 0:
            raise PatrolError("repair_command_failed")


class PatrolChecks:
    """Evaluate the fixed set of production health-check kinds."""

    def __init__(self, runner: CommandRunner):
        self.runner = runner

    @staticmethod
    def _result(item: Mapping[str, Any], status: str, code: str, evidence: Mapping[str, Any]) -> CheckResult:
        allowed = {"state", "age_seconds", "percent", "count", "date", "http_status"}
        return CheckResult(
            check_id=str(item["id"]), status=status, severity=str(item.get("severity", "critical")),
            code=_safe_identifier(code), repair_action=item.get("repair_action") if status != "healthy" else None,
            evidence={str(key): value for key, value in evidence.items() if key in allowed},
        )

    def evaluate(self, item: Mapping[str, Any], now: int) -> CheckResult:
        try:
            return getattr(self, "_" + str(item["kind"]))(item, now)
        except Exception:
            return self._result(item, "unknown", "check_failed", {})

    def _systemd(self, item: Mapping[str, Any], now: int) -> CheckResult:
        result = self.runner.command(("/usr/bin/systemctl", "is-active", str(item["target"])))
        active = result.returncode == 0 and result.stdout.strip() == "active"
        return self._result(item, "healthy" if active else "failed", "ok" if active else "not_active", {"state": "active" if active else "inactive"})

    def _docker(self, item: Mapping[str, Any], now: int) -> CheckResult:
        result = self.runner.command(("/usr/bin/docker", "inspect", "--format", "{{json .State}}", str(item["target"])))
        if result.returncode != 0:
            return self._result(item, "failed", "container_missing", {"state": "missing"})
        state = json.loads(result.stdout)
        health_info = state.get("Health") or {}
        health = health_info.get("Status", "healthy")
        healthy = bool(state.get("Running")) and health == "healthy"
        current = health if health_info else ("running" if state.get("Running") else "stopped")
        return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "container_unhealthy", {"state": current})

    def _http(self, item: Mapping[str, Any], now: int) -> CheckResult:
        parsed = urllib.parse.urlsplit(str(item["url"]))
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PatrolError("http_url_invalid")
        request = urllib.request.Request(str(item["url"]), headers={"User-Agent": "XingTuPatrol/1", "Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
        try:
            with opener.open(request, timeout=float(item.get("timeout_seconds", 10))) as response:
                code = int(response.status)
                if len(response.read(65_537)) > 65_536:
                    raise PatrolError("http_body_too_large")
        except urllib.error.HTTPError as error:
            code = int(error.code)
        healthy = 200 <= code < 300
        return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "http_failed", {"http_status": code})

    def _disk(self, item: Mapping[str, Any], now: int) -> CheckResult:
        usage = shutil.disk_usage(str(item["path"]))
        percent = round(usage.used * 100 / usage.total, 2) if usage.total else 100.0
        warning, critical = float(item.get("warning_percent", 75)), float(item.get("critical_percent", 85))
        status = "failed" if percent >= critical else "warning" if percent >= warning else "healthy"
        code = "disk_critical" if status == "failed" else "disk_warning" if status == "warning" else "ok"
        return self._result(item, status, code, {"percent": percent})

    def _backup(self, item: Mapping[str, Any], now: int) -> CheckResult:
        manifests = list(pathlib.Path(str(item["root"])).glob(str(item.get("glob", "*/SHA256SUMS"))))
        if not manifests:
            return self._result(item, "failed", "backup_missing", {})
        latest = max(manifests, key=lambda path: path.stat().st_mtime)
        age = max(0, int(now - latest.stat().st_mtime))
        healthy = self._valid_sha256_manifest(latest) and age <= int(item.get("max_age_seconds", 129600))
        return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "backup_stale_or_invalid", {"age_seconds": age})

    @staticmethod
    def _valid_sha256_manifest(manifest: pathlib.Path) -> bool:
        try:
            if manifest.is_symlink() or manifest.stat().st_size <= 0 or manifest.stat().st_size > 1024 * 1024:
                return False
            lines = manifest.read_text(encoding="ascii").splitlines()
            if not lines or len(lines) > 1000:
                return False
            directory = manifest.parent.resolve()
            for line in lines:
                if not re.fullmatch(r"[0-9a-f]{64}  [A-Za-z0-9_.-]{1,160}", line):
                    return False
                expected, name = line.split("  ", 1)
                target = (manifest.parent / name)
                if target.is_symlink() or not target.is_file() or target.resolve().parent != directory:
                    return False
                digest_hash = hashlib.sha256()
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest_hash.update(chunk)
                digest = digest_hash.hexdigest()
                if digest != expected:
                    return False
            return True
        except (OSError, UnicodeError):
            return False

    def _artifact(self, item: Mapping[str, Any], now: int) -> CheckResult:
        path = pathlib.Path(str(item["path"]))
        document = read_json(path)
        if document is None:
            return self._result(item, "failed", "artifact_missing", {})
        artifact_type = item.get("artifact_type")
        day = expected_business_day(datetime.datetime.fromtimestamp(now, BEIJING))
        if artifact_type == "ledger":
            rows = (document.get("days") or {}).get(day) if isinstance(document, dict) else None
            healthy = isinstance(rows, dict) and any(isinstance(row, dict) and row.get("collection_status") == "complete" for row in rows.values())
            return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "ledger_day_missing", {"date": day})
        if artifact_type == "audit":
            healthy = isinstance(document, dict) and document.get("date") == day
            status, code = ("healthy", "ok") if healthy else ("failed", "audit_day_missing")
            if healthy and any(isinstance(row, dict) and row.get("scan_status") != "ok" for row in document.get("channels") or []):
                status, code = "warning", "audit_channels_failed"
            return self._result(item, status, code, {"date": day})
        if artifact_type in {"generic_pricing", "video_pricing"}:
            runs = document.get("runs") if isinstance(document, dict) else None
            matches = [row for row in runs or [] if isinstance(row, dict) and row.get("date") == day]
            latest = max(matches, key=lambda row: int(row.get("generated_at") or 0)) if matches else None
            healthy = bool(latest) and not latest.get("error") and latest.get("status", "complete") != "failed"
            return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "scheduled_run_failed", {"date": day})
        age = max(0, int(now - path.stat().st_mtime))
        healthy = age <= int(item.get("max_age_seconds", 7200))
        return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "artifact_stale", {"age_seconds": age})

    def _video_sqlite(self, item: Mapping[str, Any], now: int) -> CheckResult:
        connection = sqlite3.connect(f"file:{pathlib.Path(str(item['path']))}?mode=ro", uri=True, timeout=5)
        try:
            if item.get("query") == "settlement_pending":
                count, oldest = connection.execute("select count(*), coalesce(min(created_at),0) from video_jobs where billing_status='settlement_pending'").fetchone()
                age = max(0, now - int(oldest or 0)) if oldest else 0
                healthy = int(count) == 0 or age <= int(item.get("max_age_seconds", 1800))
                return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "settlement_stalled", {"count": int(count), "age_seconds": age})
            count = int(connection.execute("select count(*) from video_webhook_outbox where status not in ('delivered','dead')").fetchone()[0])
            healthy = count <= int(item.get("max_count", 0))
            return self._result(item, "healthy" if healthy else "failed", "ok" if healthy else "webhook_backlog", {"count": count})
        finally:
            connection.close()


class RepairCoordinator:
    """Apply at most the allowed fixed actions and verify each result once."""

    def __init__(self, runner: CommandRunner, policy: Mapping[str, Any], sleeper: Callable[[float], None] = time.sleep):
        self.runner, self.policy, self.sleeper = runner, policy, sleeper

    def repair(self, results: Sequence[CheckResult], state: Mapping[str, Any], post_check: Callable[[CheckResult], CheckResult], *, now: int) -> tuple[list[CheckResult], list[dict[str, Any]], dict[str, Any]]:
        updated = dict(state)
        action_state = dict(updated.get("actions") or {})
        final, actions = [], []
        candidates_seen = 0
        budget = int(self.policy.get("max_actions_per_run", 0))
        cooldown = int(self.policy.get("repair_cooldown_seconds", 3600))
        for result in results:
            action = result.repair_action
            if result.status == "healthy" or not action:
                final.append(result)
                continue
            candidates_seen += 1
            if candidates_seen > budget:
                final.append(dataclasses.replace(result, code="repair_budget_exhausted", repair_action=None))
                continue
            previous = action_state.get(action) if isinstance(action_state.get(action), dict) else {}
            last_attempt = int(previous.get("last_attempt_at") or 0)
            if last_attempt > 0 and now - last_attempt < cooldown:
                final.append(dataclasses.replace(result, code="repair_cooldown", repair_action=None))
                continue
            action_state[action] = {"last_attempt_at": now, "check_id": result.check_id}
            try:
                self.runner.run_action(action)
                delay = float(self.policy.get("post_repair_delay_seconds", 0))
                if delay > 0:
                    self.sleeper(min(delay, 30))
                verified = post_check(result)
                repaired = verified.status == "healthy"
                actions.append({"action": action, "check_id": result.check_id, "status": "repaired" if repaired else "verification_failed"})
                final.append(verified if repaired else dataclasses.replace(verified, code="repair_verification_failed", repair_action=None))
            except Exception:
                actions.append({"action": action, "check_id": result.check_id, "status": "command_failed"})
                final.append(dataclasses.replace(result, code="repair_command_failed", repair_action=None))
        updated["actions"] = action_state
        return final, actions, updated


def observe_incidents(results: Sequence[CheckResult], state: Mapping[str, Any], *, now: int, reminder_seconds: int) -> tuple[list[IncidentEvent], dict[str, Any]]:
    """Create open/reminder/recovery events without marking delivery."""

    updated = dict(state)
    incidents = {key: dict(value) for key, value in (updated.get("incidents") or {}).items() if isinstance(value, dict)}
    events: list[IncidentEvent] = []
    for result in results:
        record = incidents.get(result.check_id, {})
        if result.status != "healthy":
            was_open = bool(record.get("open"))
            record.update({"open": True, "last_seen_at": now, "severity": result.severity, "code": result.code})
            if not was_open or not record.get("last_notified_at"):
                record["opened_at"] = now
                kind = "patrol_incident_open"
            elif now - int(record.get("last_notified_at") or 0) >= reminder_seconds:
                kind = "patrol_incident_reminder"
            else:
                kind = ""
            if kind:
                events.append(IncidentEvent(kind, result.check_id, result.severity, result.code, now))
        elif record.get("open"):
            record.update({"recovery_pending": True, "recovered_at": now, "last_seen_at": now, "code": "ok"})
            events.append(IncidentEvent("patrol_incident_recovered", result.check_id, "info", "ok", now))
        incidents[result.check_id] = record
    updated["incidents"] = incidents
    return events, updated


def record_deliveries(state: Mapping[str, Any], events: Sequence[IncidentEvent], *, now: int) -> dict[str, Any]:
    updated = dict(state)
    incidents = {key: dict(value) for key, value in (updated.get("incidents") or {}).items() if isinstance(value, dict)}
    for event in events:
        record = incidents.setdefault(event.check_id, {})
        record.update(last_notified_at=now, last_notified_kind=event.kind)
        if event.kind == "patrol_incident_recovered":
            record.update(open=False, recovery_pending=False)
    updated["incidents"] = incidents
    return updated


def notification_payload(event: IncidentEvent) -> dict[str, Any]:
    return {
        "kind": event.kind, "name": _safe_identifier(event.check_id), "code": _safe_identifier(event.code),
        "severity": event.severity if event.severity in VALID_SEVERITIES else "warning",
        "threshold": 0, "occurred_at": int(event.occurred_at),
    }


def _read_notify_token(path: str) -> str:
    source = pathlib.Path(path)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise PatrolError("notification_credential_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 16_384:
        raise PatrolError("notification_credential_unsafe")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PatrolError("notification_credential_unsafe")
    try:
        token = source.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise PatrolError("notification_credential_unavailable") from error
    if not token or any(character in token for character in "\r\n\0"):
        raise PatrolError("notification_credential_invalid")
    return token


def _notify_config(environ: Mapping[str, str]) -> tuple[str, str, str, float]:
    parsed = urllib.parse.urlsplit(str(environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_URL") or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in {"new-api", "localhost", "127.0.0.1", "::1"}
        or parsed.username or parsed.password or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment
    ):
        raise PatrolError("notification_url_invalid")
    token_file = str(environ.get("UPSTREAM_BALANCE_ALERT_ACCESS_TOKEN_FILE") or "").strip()
    user_id = str(environ.get("UPSTREAM_BALANCE_ALERT_USER_ID") or "").strip()
    try:
        timeout = float(environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_TIMEOUT") or 15)
    except ValueError as error:
        raise PatrolError("notification_configuration_invalid") from error
    if not token_file or not user_id.isdigit() or int(user_id) <= 0 or not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise PatrolError("notification_configuration_invalid")
    base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")).rstrip("/")
    return base_url, _read_notify_token(token_file), user_id, timeout


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "redirect_forbidden", headers, file_pointer)


def send_notification(event: IncidentEvent, environ: Mapping[str, str]) -> None:
    """Send a structured event through the fixed-recipient NewAPI endpoint."""

    base_url, token, user_id, timeout = _notify_config(environ)
    payload = json.dumps(notification_payload(event), separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/api/option/upstream_balance_alert", data=payload, method="POST",
        headers={
            "Authorization": token, "New-Api-User": user_id, "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": "XingTuPatrolRepair/1",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(65_537)
    except Exception as error:
        raise PatrolError("notification_delivery_failed") from error
    if len(raw) > 65_536:
        raise PatrolError("notification_delivery_failed")
    try:
        result = json.loads(raw or b"{}")
    except (UnicodeError, ValueError) as error:
        raise PatrolError("notification_delivery_failed") from error
    if not isinstance(result, dict) or result.get("success") is not True:
        raise PatrolError("notification_delivery_failed")


def run_patrol(
    policy: Mapping[str, Any], state: Mapping[str, Any], *, now: int,
    runner: CommandRunner | None = None, repair: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], list[IncidentEvent]]:
    """Run all checks, optional bounded repair, and incident observation."""

    command_runner = runner or CommandRunner()
    checks = PatrolChecks(command_runner)
    items = {str(item["id"]): item for item in policy["checks"]}
    initial = [checks.evaluate(item, now) for item in policy["checks"]]
    updated = dict(state)
    actions: list[dict[str, Any]] = []
    final = initial
    if repair:
        coordinator = RepairCoordinator(command_runner, policy)

        def post_check(previous: CheckResult) -> CheckResult:
            return checks.evaluate(items[previous.check_id], now)

        final, actions, updated = coordinator.repair(initial, updated, post_check, now=now)
    events, updated = observe_incidents(
        final, updated, now=now, reminder_seconds=int(policy["incident_reminder_seconds"]),
    )
    report = {
        "schema_version": 1,
        "generated_at": now,
        "generated_at_iso": datetime.datetime.fromtimestamp(now, BEIJING).isoformat(timespec="seconds"),
        "business_day": expected_business_day(datetime.datetime.fromtimestamp(now, BEIJING)),
        "summary": {
            "healthy": sum(row.status == "healthy" for row in final),
            "warning": sum(row.status == "warning" for row in final),
            "failed": sum(row.status == "failed" for row in final),
            "unknown": sum(row.status == "unknown" for row in final),
            "actions": len(actions),
            "notifications": len(events),
        },
        "checks": [row.to_dict() for row in final],
        "actions": actions,
    }
    updated["schema_version"] = 1
    updated["last_run_at"] = now
    return report, updated, events
