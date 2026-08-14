#!/usr/bin/env python3
"""Build and deliver one private Beijing-time upstream operations digest."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_UPSTREAMS_PATH = ROOT / "upstreams.json"
DEFAULT_LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"
DEFAULT_AUDIT_PATH = ROOT / "data" / "daily-upstream-audit.json"
DEFAULT_PRICING_LOG_PATH = ROOT / "data" / "auto-pricing-log.json"
DEFAULT_LIVE_BALANCE_PATH = ROOT / "data" / "upstream-balance-live.json"
DEFAULT_STATE_PATH = ROOT / "data" / "daily-ops-digest-state.json"
BEIJING = ZoneInfo("Asia/Shanghai")
MAX_CHANNELS = 100
MAX_PAYLOAD_BYTES = 64 * 1024
SAFE_CODE = re.compile(r"^[a-z0-9_.-]{1,80}$")


class DigestError(RuntimeError):
    """A sanitized, retryable digest generation or delivery error."""


class NotifyConfig(NamedTuple):
    base_url: str
    token: str
    user_id: str
    timeout: float


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise urllib.error.HTTPError(
            request.full_url, code, "notification redirects are forbidden", headers, file_pointer
        )


def read_json(path: pathlib.Path, default: Any = None, *, required: bool = False) -> Any:
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not required:
            return default
        raise DigestError("required_digest_input_missing") from None
    except (OSError, ValueError, TypeError) as error:
        raise DigestError("digest_input_invalid") from error


def write_private_json(path: pathlib.Path, value: Any) -> None:
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


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _count(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return result if result >= 0 else 0


def _safe_name(value: Any, fallback: str) -> str:
    name = " ".join(str(value or "").split()).strip()
    if not name:
        name = fallback
    return name[:80]


def _safe_code(value: Any, fallback: str = "unknown") -> str:
    code = str(value or "").strip().lower()
    return code if SAFE_CODE.fullmatch(code) else fallback


def target_beijing_day(now: datetime.datetime | None = None) -> str:
    current = now.astimezone(BEIJING) if now else datetime.datetime.now(BEIJING)
    return (current.date() - datetime.timedelta(days=1)).isoformat()


def _latest_pricing_run(pricing_history: Any, day: str) -> dict[str, Any]:
    runs = pricing_history.get("runs") if isinstance(pricing_history, dict) else None
    matches = [row for row in runs or [] if isinstance(row, dict) and row.get("date") == day]
    if not matches:
        raise DigestError("pricing_run_missing")
    return max(matches, key=lambda row: _count(row.get("generated_at")))


def _month_totals(ledger: dict[str, Any], day: str, slug: str) -> tuple[int, float]:
    month = day[:7] + "-"
    calls = 0
    cost = 0.0
    for date, providers in (ledger.get("days") or {}).items():
        if not str(date).startswith(month) or str(date) > day or not isinstance(providers, dict):
            continue
        row = providers.get(slug)
        if not isinstance(row, dict) or row.get("collection_status") != "complete":
            continue
        calls += _count(row.get("day_log_rows"))
        amount = _number(row.get("day_log_cost_cny"))
        if amount is not None:
            cost += amount
    return calls, round(cost, 8)


def build_digest(
    upstreams: Any,
    ledger: Any,
    audit: Any,
    pricing_history: Any,
    live_balance: Any,
    day: str,
    *,
    generated_at: int,
) -> dict[str, Any]:
    """Build a bounded digest from private monitoring artifacts."""
    day_rows = (ledger.get("days") or {}).get(day) if isinstance(ledger, dict) else None
    if not isinstance(day_rows, dict):
        raise DigestError("ledger_day_missing")
    if not isinstance(audit, dict) or audit.get("date") != day:
        raise DigestError("audit_day_missing")
    pricing_run = _latest_pricing_run(pricing_history, day)
    live = live_balance.get("providers") if isinstance(live_balance, dict) else {}
    audit_by_slug = {
        str(row.get("upstream_slug") or "").strip(): row
        for row in audit.get("channels") or []
        if isinstance(row, dict)
    }

    channels = []
    for configured in upstreams if isinstance(upstreams, list) else []:
        if not isinstance(configured, dict) or configured.get("enabled", True) is False:
            continue
        slug = str(configured.get("slug") or "").strip()
        if not slug or len(channels) >= MAX_CHANNELS:
            continue
        daily = day_rows.get(slug) if isinstance(day_rows.get(slug), dict) else {}
        current = live.get(slug) if isinstance(live, dict) and isinstance(live.get(slug), dict) else {}
        month_calls, month_cost = _month_totals(ledger, day, slug)
        collection_status = _safe_code(daily.get("collection_status"), "unknown")
        balance_status = _safe_code(current.get("status"), "unknown")
        daily_cost = _number(daily.get("day_log_cost_cny")) if collection_status == "complete" else None
        balance = _number(current.get("balance_usd")) if balance_status == "complete" else None
        audit_status = _safe_code((audit_by_slug.get(slug) or {}).get("scan_status"), "unknown")
        channels.append(
            {
                "slug": slug[:80],
                "name": _safe_name(configured.get("name"), slug),
                "collection_status": collection_status,
                "audit_status": audit_status,
                "balance_status": balance_status,
                "balance": balance,
                "daily_calls": _count(daily.get("day_log_rows")) if collection_status == "complete" else 0,
                "daily_cost_cny": round(daily_cost, 8) if daily_cost is not None else None,
                "month_calls": month_calls,
                "month_cost_cny": month_cost,
            }
        )
    if not channels:
        raise DigestError("digest_channels_missing")

    decisions = [row for row in pricing_run.get("decisions") or [] if isinstance(row, dict)]
    reasons = Counter(_safe_code(row.get("reason")) for row in decisions)
    blocked_reasons = {"upstream_collection_incomplete", "critical_model_alert"}
    pricing_status = _safe_code(pricing_run.get("status"), "complete")
    if pricing_run.get("error"):
        pricing_status = "failed"
    channel_rows = [row for row in audit.get("channels") or [] if isinstance(row, dict)]
    report = {
        "schema_version": 1,
        "date": day,
        "generated_at": int(generated_at),
        "channels": channels,
        "pricing": {
            "status": pricing_status,
            "discovered": len(decisions),
            "applied": sum(row.get("action") == "apply" for row in decisions),
            "skipped": sum(row.get("action") != "apply" for row in decisions),
            "blocked": sum(_safe_code(row.get("reason")) in blocked_reasons for row in decisions),
            "protected_video": reasons.get("video_official_pricing_only", 0),
            "reasons": dict(sorted(reasons.items())[:30]),
        },
        "audit": {
            "ok_channels": sum(row.get("scan_status") == "ok" for row in channel_rows),
            "failed_channels": sum(row.get("scan_status") != "ok" for row in channel_rows),
            "alerts": len([row for row in audit.get("alerts") or [] if isinstance(row, dict)]),
        },
    }
    payload = notification_payload(report)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise DigestError("digest_payload_too_large")
    return report


def notification_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Remove private routing identifiers and keep only bounded report fields."""
    channels = []
    for row in report.get("channels") or []:
        channels.append({key: row.get(key) for key in (
            "name", "collection_status", "audit_status", "balance_status", "balance",
            "daily_calls", "daily_cost_cny", "month_calls", "month_cost_cny",
        )})
    return {
        "date": report.get("date"),
        "generated_at": report.get("generated_at"),
        "channels": channels,
        "pricing": report.get("pricing"),
        "audit": report.get("audit"),
    }


def _read_token(path: str) -> str:
    source = pathlib.Path(path)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise DigestError("notification_credential_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DigestError("notification_credential_unsafe")
    if metadata.st_size <= 0 or metadata.st_size > 16_384:
        raise DigestError("notification_credential_unsafe")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise DigestError("notification_credential_unsafe")
    try:
        token = source.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise DigestError("notification_credential_unavailable") from error
    if not token or len(token) > 16_384 or any(character in token for character in "\r\n\0"):
        raise DigestError("notification_credential_invalid")
    return token


def _notify_config(environ: dict[str, str] | os._Environ[str]) -> NotifyConfig:
    raw_url = (
        environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_URL")
        or environ.get("NEWAPI_SETTLEMENT_URL")
        or ""
    )
    parsed = urllib.parse.urlsplit(str(raw_url).strip())
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in {"new-api", "localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DigestError("notification_url_invalid")
    token_file = str(
        environ.get("UPSTREAM_BALANCE_ALERT_ACCESS_TOKEN_FILE")
        or environ.get("NEWAPI_SETTLEMENT_ACCESS_TOKEN_FILE")
        or ""
    ).strip()
    user_id = str(
        environ.get("UPSTREAM_BALANCE_ALERT_USER_ID")
        or environ.get("NEWAPI_SETTLEMENT_USER_ID")
        or ""
    ).strip()
    if not token_file or not user_id.isdigit() or int(user_id) <= 0:
        raise DigestError("notification_configuration_incomplete")
    try:
        timeout = float(environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_TIMEOUT") or 15)
    except ValueError as error:
        raise DigestError("notification_configuration_invalid") from error
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise DigestError("notification_configuration_invalid")
    base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")).rstrip("/")
    return NotifyConfig(base_url, _read_token(token_file), user_id, timeout)


def send_digest(report: dict[str, Any], environ: dict[str, str] | os._Environ[str]) -> None:
    config = _notify_config(environ)
    payload = json.dumps(notification_payload(report), ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise DigestError("digest_payload_too_large")
    request = urllib.request.Request(
        config.base_url + "/api/option/upstream_ops_digest",
        data=payload,
        method="POST",
        headers={
            "Authorization": config.token,
            "New-Api-User": config.user_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "XingTuDailyOpsDigest/1",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
    try:
        with opener.open(request, timeout=config.timeout) as response:
            body = response.read(65_537)
    except Exception as error:
        raise DigestError("digest_delivery_failed") from error
    if len(body) > 65_536:
        raise DigestError("digest_delivery_failed")
    try:
        result = json.loads(body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DigestError("digest_delivery_failed") from error
    if not isinstance(result, dict) or result.get("success") is not True:
        raise DigestError("digest_delivery_failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date")
    parser.add_argument("--upstreams", type=pathlib.Path, default=DEFAULT_UPSTREAMS_PATH)
    parser.add_argument("--ledger", type=pathlib.Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--audit", type=pathlib.Path, default=DEFAULT_AUDIT_PATH)
    parser.add_argument("--pricing-log", type=pathlib.Path, default=DEFAULT_PRICING_LOG_PATH)
    parser.add_argument("--live-balance", type=pathlib.Path, default=DEFAULT_LIVE_BALANCE_PATH)
    parser.add_argument("--state", type=pathlib.Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None, environ: dict[str, str] | os._Environ[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    day = str(args.date or target_beijing_day())
    try:
        datetime.date.fromisoformat(day)
        state = read_json(args.state, {"schema_version": 1, "delivered_dates": {}})
        delivered = state.get("delivered_dates") if isinstance(state, dict) else None
        if not isinstance(delivered, dict):
            raise DigestError("digest_state_invalid")
        if day in delivered and not args.force:
            print(json.dumps({"status": "already_delivered", "date": day}, sort_keys=True))
            return 0
        now = int(time.time())
        report = build_digest(
            read_json(args.upstreams, required=True),
            read_json(args.ledger, required=True),
            read_json(args.audit, required=True),
            read_json(args.pricing_log, required=True),
            read_json(args.live_balance, required=True),
            day,
            generated_at=now,
        )
        if args.dry_run:
            print(json.dumps({"status": "dry_run", "date": day, "channels": len(report["channels"])}, sort_keys=True))
            return 0
        send_digest(report, env)
        fingerprint = hashlib.sha256(
            json.dumps(notification_payload(report), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        delivered[day] = {"delivered_at": now, "digest_sha256": fingerprint}
        state["schema_version"] = 1
        state["delivered_dates"] = dict(sorted(delivered.items())[-62:])
        write_private_json(args.state, state)
        print(json.dumps({"status": "delivered", "date": day, "channels": len(report["channels"])}, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "digest_failed", "date": day}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
