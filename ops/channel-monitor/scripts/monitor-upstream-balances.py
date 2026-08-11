#!/usr/bin/env python3
"""Probe live upstream balances and send deduplicated operator email alerts."""

import argparse
import datetime
import importlib.util
import json
import math
import os
import pathlib
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parent.parent
COLLECTOR_PATH = pathlib.Path(__file__).resolve().parent / "fetch-upstream-balance.py"
DEFAULT_UPSTREAMS_PATH = ROOT / "upstreams.json"
DEFAULT_CREDENTIALS_PATH = ROOT / "upstream-credentials.json"
DEFAULT_SNAPSHOT_PATH = ROOT / "data" / "upstream-balance-live.json"
DEFAULT_STATE_PATH = ROOT / "data" / "upstream-balance-alert-state.json"
BEIJING = ZoneInfo("Asia/Shanghai")


def _load_collector():
    spec = importlib.util.spec_from_file_location("balance_alert_collector", COLLECTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("balance collector module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COLLECTOR = _load_collector()


class AlertEvent(NamedTuple):
    kind: str
    slug: str
    name: str
    balance: float | None
    threshold: float
    occurred_at: int


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


def read_json(path, default, *, required=False):
    source = pathlib.Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not required:
            return default
        raise RuntimeError("required monitor configuration is unavailable") from None
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("monitor JSON state is invalid") from exc


def write_private_json(path, value):
    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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


def beijing_iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, BEIJING).isoformat(timespec="seconds")


def safe_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_token_file(path, platform_name=os.name):
    token_path = pathlib.Path(path)
    try:
        metadata = token_path.stat()
    except OSError as exc:
        raise RuntimeError("NewAPI access-token file cannot be read") from exc
    if metadata.st_size <= 0 or metadata.st_size > 16_384:
        raise RuntimeError("NewAPI access-token file has invalid size")
    if platform_name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError("NewAPI access-token file permissions must be 0600")
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("NewAPI access-token file cannot be read") from exc
    if not token or "\x00" in token or "\r" in token or "\n" in token:
        raise RuntimeError("NewAPI access-token file is malformed")
    return token


def _positive_int(value, default, label):
    try:
        number = int(str(value or default))
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc
    if number <= 0:
        raise ValueError(f"invalid {label}")
    return number


def validate_notify_url(value):
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    hostname = (parsed.hostname or "").lower()
    internal_host = hostname in {
        "new-api",
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        not parsed.netloc
        or parsed.scheme not in {"http", "https"}
        or not internal_host
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("NewAPI notification URL is not approved")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    ).rstrip("/")


def load_notify_config(environ):
    base_url = validate_notify_url(
        environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_URL")
        or environ.get("NEWAPI_SETTLEMENT_URL")
        or ""
    )
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
        raise ValueError("incomplete NewAPI notification configuration")
    token = read_token_file(token_file)
    timeout = float(environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_TIMEOUT") or 15)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise ValueError("invalid NewAPI notification timeout")
    return NotifyConfig(base_url, token, user_id, timeout)


def select_targets(upstreams, credentials):
    targets = []
    for item in upstreams if isinstance(upstreams, list) else []:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        slug = str(item.get("slug") or "").strip()
        credential = credentials.get(slug) if isinstance(credentials, dict) else None
        if not slug or not isinstance(credential, dict):
            continue
        name = str(item.get("name") or slug).strip() or slug
        website = str(item.get("website_url") or "").strip()
        targets.append((slug, name, credential, website))
    return sorted(targets, key=lambda row: row[0])


def probe_targets(targets, now=None):
    checked_at = int(time.time() if now is None else now)
    providers = {}
    for slug, name, credential, website in targets:
        try:
            result = COLLECTOR.probe_balance(slug, credential, website)
            balance = safe_number(result.get("balance_usd"))
            if result.get("status") != "complete" or balance is None:
                raise RuntimeError("balance unavailable")
            providers[slug] = {
                "name": name,
                "status": "complete",
                "balance_usd": balance,
                "billing_api": str(result.get("billing_api") or "unknown"),
                "checked_at": checked_at,
                "checked_at_iso": beijing_iso(checked_at),
            }
        except Exception:
            providers[slug] = {
                "name": name,
                "status": "unknown",
                "balance_usd": None,
                "billing_api": None,
                "error_code": "probe_failed",
                "checked_at": checked_at,
                "checked_at_iso": beijing_iso(checked_at),
            }
    return {
        "schema_version": 1,
        "updated_at": checked_at,
        "updated_at_iso": beijing_iso(checked_at),
        "providers": providers,
    }


def observe_provider(
    slug,
    name,
    row,
    prior_record,
    *,
    threshold,
    now,
    reminder_seconds=86_400,
    failure_threshold=3,
):
    record = dict(prior_record or {})
    events = []
    balance = safe_number(row.get("balance_usd"))
    complete = row.get("status") == "complete" and balance is not None
    record["last_observed_at"] = int(now)

    if not complete:
        record["last_probe_status"] = "unknown"
        record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
        if (
            record["consecutive_failures"] >= failure_threshold
            and not record.get("collection_failure_open")
        ):
            events.append(AlertEvent("balance_collection_failed", slug, name, None, threshold, now))
        return events, record

    record["last_probe_status"] = "complete"
    record["last_balance_usd"] = balance
    record["consecutive_failures"] = 0
    if record.get("collection_failure_open"):
        events.append(
            AlertEvent("balance_collection_recovered", slug, name, balance, threshold, now)
        )

    if balance <= threshold:
        record["balance_state"] = "depleted"
        last_alert = int(record.get("last_depletion_alert_at") or 0)
        if not record.get("depletion_open"):
            events.append(AlertEvent("balance_depleted", slug, name, balance, threshold, now))
        elif int(now) - last_alert >= reminder_seconds:
            events.append(
                AlertEvent("balance_depleted_reminder", slug, name, balance, threshold, now)
            )
    else:
        record["balance_state"] = "healthy"
        if record.get("depletion_open"):
            events.append(AlertEvent("balance_recovered", slug, name, balance, threshold, now))
    return events, record


def record_delivery(record, event, now):
    updated = dict(record)
    if event.kind in {"balance_depleted", "balance_depleted_reminder"}:
        updated["depletion_open"] = True
        updated["last_depletion_alert_at"] = int(now)
    elif event.kind == "balance_recovered":
        updated["depletion_open"] = False
        updated["last_balance_recovery_at"] = int(now)
    elif event.kind == "balance_collection_failed":
        updated["collection_failure_open"] = True
        updated["last_collection_failure_alert_at"] = int(now)
    elif event.kind == "balance_collection_recovered":
        updated["collection_failure_open"] = False
        updated["last_collection_recovery_at"] = int(now)
    return updated


def send_event(config, event):
    payload = json.dumps(
        {
            "kind": event.kind,
            "name": event.name,
            "balance": event.balance,
            "threshold": event.threshold,
            "occurred_at": event.occurred_at,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        config.base_url + "/api/option/upstream_balance_alert",
        data=payload,
        method="POST",
        headers={
            "Authorization": config.token,
            "New-Api-User": config.user_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "XingTuUpstreamBalanceMonitor/1",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler())
    with opener.open(request, timeout=config.timeout) as response:
        raw = response.read(65_537)
    if len(raw) > 65_536:
        raise RuntimeError("NewAPI notification response exceeds 64 KiB")
    result = json.loads(raw or b"{}")
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("NewAPI notification response is invalid")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstreams", type=pathlib.Path, default=DEFAULT_UPSTREAMS_PATH)
    parser.add_argument("--credentials", type=pathlib.Path, default=DEFAULT_CREDENTIALS_PATH)
    parser.add_argument("--snapshot", type=pathlib.Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--state", type=pathlib.Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-email", action="store_true")
    return parser


def _float_setting(environ, key, default, minimum=None):
    try:
        value = float(environ.get(key) or default)
    except ValueError as exc:
        raise ValueError(f"invalid {key}") from exc
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(f"invalid {key}")
    return value


def main(argv=None, environ=None):
    args = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    now = int(time.time())
    threshold = _float_setting(env, "UPSTREAM_BALANCE_ALERT_THRESHOLD", 0)
    reminder_hours = _float_setting(
        env, "UPSTREAM_BALANCE_ALERT_REMINDER_HOURS", 24, minimum=1
    )
    failure_threshold = _positive_int(
        env.get("UPSTREAM_BALANCE_ALERT_FAILURE_THRESHOLD"), 3, "failure threshold"
    )

    if args.test_email:
        try:
            config = load_notify_config(env)
            send_event(
                config,
                AlertEvent("test", "test", "监控程序", None, threshold, now),
            )
        except Exception:
            print(json.dumps({"status": "email_delivery_failed"}, sort_keys=True))
            return 2
        print(json.dumps({"status": "test_email_sent"}, sort_keys=True))
        return 0

    try:
        upstreams = read_json(args.upstreams, [], required=True)
        credentials = read_json(args.credentials, {}, required=True)
        prior_state = read_json(args.state, {"schema_version": 1, "providers": {}})
        if not isinstance(upstreams, list) or not isinstance(credentials, dict):
            raise RuntimeError("monitor configuration has an invalid shape")
        if not isinstance(prior_state, dict) or not isinstance(prior_state.get("providers"), dict):
            raise RuntimeError("monitor state has an invalid shape")
        targets = select_targets(upstreams, credentials)
        if not targets:
            raise RuntimeError("monitor has no enabled credentialed targets")
    except RuntimeError:
        print(json.dumps({"status": "monitor_configuration_invalid"}, sort_keys=True))
        return 2
    snapshot = probe_targets(targets, now=now)
    write_private_json(args.snapshot, snapshot)
    state = {"schema_version": 1, "providers": dict(prior_state.get("providers") or {})}
    planned = []
    for slug, row in snapshot["providers"].items():
        events, record = observe_provider(
            slug,
            row.get("name") or slug,
            row,
            state["providers"].get(slug),
            threshold=threshold,
            now=now,
            reminder_seconds=int(reminder_hours * 3600),
            failure_threshold=failure_threshold,
        )
        state["providers"][slug] = record
        planned.extend(events)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "targets": len(targets),
                    "complete": sum(
                        row.get("status") == "complete"
                        for row in snapshot["providers"].values()
                    ),
                    "unknown": sum(
                        row.get("status") != "complete"
                        for row in snapshot["providers"].values()
                    ),
                    "events": [
                        {"kind": event.kind, "slug": event.slug} for event in planned
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    delivery_failures = 0
    config = None
    if planned:
        try:
            config = load_notify_config(env)
        except Exception:
            delivery_failures = len(planned)
    if config is not None:
        for event in planned:
            try:
                send_event(config, event)
            except Exception:
                delivery_failures += 1
            else:
                state["providers"][event.slug] = record_delivery(
                    state["providers"][event.slug], event, now
                )
    state["updated_at"] = now
    state["updated_at_iso"] = beijing_iso(now)
    write_private_json(args.state, state)

    unknown = sum(
        row.get("status") != "complete" for row in snapshot["providers"].values()
    )
    print(
        json.dumps(
            {
                "status": "complete" if not delivery_failures else "email_delivery_failed",
                "targets": len(targets),
                "complete": len(targets) - unknown,
                "unknown": unknown,
                "events_planned": len(planned),
                "events_delivered": len(planned) - delivery_failures,
            },
            sort_keys=True,
        )
    )
    return 2 if delivery_failures else 0


if __name__ == "__main__":
    sys.exit(main())
