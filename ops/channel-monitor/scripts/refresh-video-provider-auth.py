#!/usr/bin/env python3
"""Refresh renewable video billing sessions and inspect CAPTCHA-bound tokens."""

from __future__ import annotations

import argparse
import http.cookiejar
import importlib.util
import json
import os
import pathlib
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
GATEWAY_ROOT = ROOT.parent / "video-job-gateway"
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from credential_lifecycle import (  # noqa: E402
    CredentialLifecycleError,
    atomic_install_captcha_token,
    atomic_write_json,
    inspect_captcha_token,
    warning_event,
)


DEFAULT_CONFIG = ROOT / "config" / "video-provider-auth-lifecycle.json"
DEFAULT_CREDENTIALS = ROOT / "config" / "upstream-credentials.json"
DEFAULT_STATE = ROOT / "data" / "video-provider-auth-state.json"
BALANCE_MONITOR = pathlib.Path(__file__).with_name("monitor-upstream-balances.py")
MAX_JSON_BYTES = 128 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # noqa: ANN001
        return None


def load_private_json(path: pathlib.Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("private JSON path is not a regular file")
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise RuntimeError("private JSON file size is unsafe")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError("private JSON permissions must be 0600")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("private JSON root must be an object")
    return value


def load_private_text(path: pathlib.Path) -> str:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("private credential path is not a regular file")
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise RuntimeError("private credential file size is unsafe")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError("private credential permissions must be 0600")
    return path.read_text(encoding="utf-8").strip()


def load_public_config(path: pathlib.Path):
    value = json.loads(path.read_text(encoding="utf-8"))
    providers = value.get("providers") if isinstance(value, dict) else None
    if not isinstance(providers, list):
        raise RuntimeError("auth lifecycle config requires a providers list")
    return value


def approved_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("provider base URL must be an HTTPS origin")
    return urllib.parse.urlunsplit(("https", parsed.netloc, "", "", ""))


def refresh_newapi_session(
    provider: dict,
    source: dict,
    *,
    now: int,
    opener=None,
) -> dict:
    provider_id = str(provider.get("provider_id") or "").strip().lower()
    origin = approved_origin(provider.get("base_url"))
    username = str(source.get("username") or "").strip()
    password = str(source.get("password") or "")
    if not provider_id or not username or not password:
        raise RuntimeError("provider login credentials are incomplete")
    cookie_jar = http.cookiejar.CookieJar()
    client = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(cookie_jar),
        NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    login_request = urllib.request.Request(
        origin + "/api/user/login",
        data=json.dumps({"username": username, "password": password}, separators=(",", ":")).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "XingTuVideoAuthRefresh/1"},
    )
    raw = _read_json(client, login_request, timeout_seconds=int(provider.get("timeout_seconds") or 15))
    if raw.get("success") is not True or not isinstance(raw.get("data"), dict):
        raise RuntimeError("provider login rejected")
    data = raw["data"]
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    user_id = str(user.get("id") or "").strip()
    authorization = ""
    if str(data.get("access_token") or "").strip():
        authorization = "Bearer " + str(data["access_token"]).strip()
    cookie = "; ".join(f"{item.name}={item.value}" for item in cookie_jar)
    if not user_id.isdigit() or not (authorization or cookie):
        raise RuntimeError("provider login returned no usable read-only session")
    headers = {"Accept": "application/json", "New-Api-User": user_id, "User-Agent": "XingTuVideoAuthRefresh/1"}
    if authorization:
        headers["Authorization"] = authorization
    if cookie:
        headers["Cookie"] = cookie
    probe = urllib.request.Request(origin + "/api/task/self?p=1&page_size=1", method="GET", headers=headers)
    probe_raw = _read_json(client, probe, timeout_seconds=int(provider.get("timeout_seconds") or 15))
    if probe_raw.get("success") is not True:
        raise RuntimeError("provider billing session verification failed")
    lease_seconds = max(1800, min(int(provider.get("lease_seconds") or 7200), 86400))
    return {
        "schema_version": 1,
        "provider_id": provider_id,
        "credential_kind": "newapi_read_only_session",
        "authorization": authorization,
        "cookie": cookie,
        "new_api_user": user_id,
        "issued_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(now + lease_seconds, timezone.utc).isoformat(timespec="seconds"),
    }


def _read_json(opener, request, *, timeout_seconds: int) -> dict:
    try:
        with opener.open(request, timeout=max(3, min(timeout_seconds, 60))) as response:
            if not 200 <= int(getattr(response, "status", 0) or 0) < 300:
                raise RuntimeError("provider authentication HTTP error")
            body = response.read(MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as error:
        if int(error.code or 0) in {401, 403}:
            raise RuntimeError("provider authentication rejected") from error
        raise RuntimeError("provider authentication HTTP error") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError("provider authentication unavailable") from error
    if len(body) > MAX_JSON_BYTES:
        raise RuntimeError("provider authentication response too large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("provider authentication response invalid") from error
    if not isinstance(value, dict):
        raise RuntimeError("provider authentication response invalid")
    return value


def run(config: dict, credentials: dict, state: dict, *, now: int, dry_run: bool) -> dict:
    results = []
    events = []
    for provider in config.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("enabled", True) is False:
            continue
        provider_id = str(provider.get("provider_id") or "").strip().lower()
        mode = str(provider.get("refresh_mode") or "").strip().lower()
        try:
            if mode == "scheduled_login":
                source_slug = str(provider.get("credential_source_slug") or provider_id).strip()
                source = credentials.get(source_slug)
                if not isinstance(source, dict):
                    raise RuntimeError("credential source is missing")
                document = refresh_newapi_session(provider, source, now=now)
                if not dry_run:
                    atomic_write_json(pathlib.Path(str(provider.get("output_file") or "")), document)
                provider_state = state.setdefault(provider_id, {})
                if isinstance(provider_state, dict) and provider_state.pop("refresh_failure_open", False):
                    events.append(
                        {
                            "kind": "credential_refresh_recovered",
                            "provider_id": provider_id,
                            "threshold_days": 0,
                            "occurred_at": now,
                        }
                    )
                results.append({"provider_id": provider_id, "status": "refreshed", "refresh_mode": mode})
            elif mode == "captcha_bound":
                token_path = pathlib.Path(str(provider.get("token_file") or ""))
                token = load_private_text(token_path)
                status = inspect_captcha_token(
                    token,
                    provider_id=provider_id,
                    now=now,
                    expected_issuer=str(provider.get("expected_issuer") or ""),
                    expected_audience=str(provider.get("expected_audience") or ""),
                )
                event = warning_event(
                    status,
                    state,
                    thresholds_days=provider.get("warning_days") or (30, 14, 7, 3, 1),
                    now=now,
                )
                if event:
                    events.append(event)
                results.append(
                    {
                        "provider_id": provider_id,
                        "status": status.state,
                        "refresh_mode": mode,
                        "expires_at": status.expires_at,
                        "remaining_seconds": status.remaining_seconds,
                    }
                )
            elif mode == "static_key":
                results.append({"provider_id": provider_id, "status": "externally_managed", "refresh_mode": mode})
            else:
                raise RuntimeError("unsupported refresh mode")
        except Exception as error:
            provider_state = state.setdefault(provider_id, {})
            if isinstance(provider_state, dict) and not provider_state.get("refresh_failure_open"):
                provider_state["refresh_failure_open"] = True
                events.append(
                    {
                        "kind": "credential_refresh_failed",
                        "provider_id": provider_id,
                        "threshold_days": 0,
                        "occurred_at": now,
                    }
                )
            results.append(
                {
                    "provider_id": provider_id,
                    "status": "failed",
                    "refresh_mode": mode or "unknown",
                    "error_code": (
                        error.code if isinstance(error, CredentialLifecycleError) else "credential_refresh_failed"
                    ),
                }
            )
    return {"schema_version": 1, "checked_at": now, "results": results, "events": events}


def deliver_events(events: list[dict], environ) -> None:
    if not events or not environ.get("UPSTREAM_BALANCE_ALERT_NOTIFY_URL"):
        return
    spec = importlib.util.spec_from_file_location("video_auth_alert_transport", BALANCE_MONITOR)
    transport = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = transport
    spec.loader.exec_module(transport)
    notify = transport.load_notify_config(environ)
    for value in events:
        transport.send_event(
            notify,
            transport.AlertEvent(
                kind=value["kind"],
                slug=value["provider_id"],
                name=value["provider_id"],
                balance=None,
                threshold=float(value.get("threshold_days") or 0),
                occurred_at=int(value["occurred_at"]),
            ),
        )


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    parser.add_argument("--credentials", type=pathlib.Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--state", type=pathlib.Path, default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--install-provider", choices=("toonflow",))
    parser.add_argument("--replacement-token-file", type=pathlib.Path)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = load_public_config(args.config)
    now = int(time.time())
    if args.install_provider:
        if not args.replacement_token_file:
            raise SystemExit("--replacement-token-file is required")
        provider = next(
            (row for row in config["providers"] if row.get("provider_id") == args.install_provider),
            None,
        )
        if not isinstance(provider, dict) or provider.get("refresh_mode") != "captcha_bound":
            raise SystemExit("provider is not CAPTCHA-bound")
        replacement = load_private_text(args.replacement_token_file)
        status = atomic_install_captcha_token(
            provider["token_file"],
            replacement,
            provider_id=args.install_provider,
            now=now,
            expected_issuer=str(provider.get("expected_issuer") or ""),
            expected_audience=str(provider.get("expected_audience") or ""),
        )
        print(json.dumps({"provider_id": args.install_provider, "status": status.state, "expires_at": status.expires_at}))
        return 0
    credentials = load_private_json(args.credentials)
    state = {}
    if args.state.exists():
        state = load_private_json(args.state)
    report = run(config, credentials, state, now=now, dry_run=args.dry_run)
    if not args.dry_run:
        deliver_events(report["events"], os.environ)
        atomic_write_json(args.state, state)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 2 if any(row["status"] == "failed" for row in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
