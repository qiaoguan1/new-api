#!/usr/bin/env python3
"""Loopback-only, unprivileged control API for the patrol repair worker."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import pathlib
import re
import stat
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


SAFE_IDENTIFIER = re.compile(r"^[a-z0-9_.-]{1,80}$")
MAX_TOKEN_BYTES = 1024
MAX_STATUS_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 1024


class ApiError(RuntimeError):
    """A sanitized API configuration or storage failure."""


def validate_bind(host: str, port: int) -> tuple[str, int]:
    if host != "127.0.0.1":
        raise ApiError("bind_not_loopback")
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise ApiError("port_invalid")
    return host, port


def read_token(path: pathlib.Path) -> str:
    source = pathlib.Path(path)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise ApiError("token_file_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > MAX_TOKEN_BYTES:
        raise ApiError("token_file_unsafe")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ApiError("token_file_unsafe")
    try:
        token = source.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise ApiError("token_file_unavailable") from error
    if len(token) < 32 or any(character in token for character in "\r\n\0"):
        raise ApiError("token_invalid")
    return token


def authorized(header: str | None, expected_token: str) -> bool:
    prefix = "Bearer "
    value = str(header or "")
    if not value.startswith(prefix):
        return False
    return hmac.compare_digest(value[len(prefix):], expected_token)


def _safe(value: Any, fallback: str = "redacted") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if SAFE_IDENTIFIER.fullmatch(candidate) else fallback


def read_status(path: pathlib.Path) -> dict[str, Any]:
    source = pathlib.Path(path)
    try:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATUS_BYTES:
            raise ApiError("status_file_unsafe")
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "status": "not_run"}
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise ApiError("status_file_invalid") from error
    if not isinstance(document, dict):
        raise ApiError("status_file_invalid")
    summary = document.get("summary") if isinstance(document.get("summary"), dict) else {}
    safe_summary = {}
    for key in ("healthy", "warning", "failed", "unknown", "actions", "notifications", "notifications_delivered"):
        try:
            safe_summary[key] = max(0, int(summary.get(key) or 0))
        except (TypeError, ValueError):
            safe_summary[key] = 0
    incidents = []
    for row in document.get("incidents") or []:
        if not isinstance(row, dict) or len(incidents) >= 100:
            continue
        incidents.append({
            "check_id": _safe(row.get("check_id")),
            "status": _safe(row.get("status")),
            "severity": _safe(row.get("severity")),
            "code": _safe(row.get("code")),
        })
    try:
        generated_at = max(0, int(document.get("generated_at") or 0))
    except (TypeError, ValueError):
        generated_at = 0
    return {
        "schema_version": 1,
        "status": "available",
        "generated_at": generated_at,
        "generated_at_iso": str(document.get("generated_at_iso") or "")[:40],
        "summary": safe_summary,
        "incidents": incidents,
    }


def write_trigger(path: pathlib.Path, *, now: int | None = None) -> None:
    destination = pathlib.Path(path)
    parent = destination.parent
    try:
        metadata = parent.lstat()
    except OSError as error:
        raise ApiError("trigger_directory_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ApiError("trigger_directory_unsafe")
    temporary = parent / (destination.name + f".tmp.{os.getpid()}")
    payload = json.dumps({"requested_at": int(time.time() if now is None else now)}, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class PatrolHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 8


def handler_factory(token: str, status_path: pathlib.Path, trigger_path: pathlib.Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "XingTuPatrolAPI/1"
        sys_version = ""

        def _json(self, code: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authenticated(self) -> bool:
            if authorized(self.headers.get("Authorization"), token):
                return True
            self._json(401, {"success": False, "code": "unauthorized"})
            return False

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(200, {"success": True, "status": "ok"})
                return
            if self.path != "/v1/status":
                self._json(404, {"success": False, "code": "not_found"})
                return
            if not self._authenticated():
                return
            try:
                self._json(200, {"success": True, "data": read_status(status_path)})
            except ApiError:
                self._json(503, {"success": False, "code": "status_unavailable"})

        def do_POST(self) -> None:
            if self.path != "/v1/run":
                self._json(404, {"success": False, "code": "not_found"})
                return
            if not self._authenticated():
                return
            if self.headers.get("Transfer-Encoding") or len(self.headers) > 50:
                self._json(400, {"success": False, "code": "invalid_request"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._json(400, {"success": False, "code": "invalid_request"})
                return
            if length < 0 or length > MAX_REQUEST_BYTES:
                self._json(413, {"success": False, "code": "request_too_large"})
                return
            if length:
                self.rfile.read(length)
            try:
                write_trigger(trigger_path)
                self._json(202, {"success": True, "status": "accepted"})
            except ApiError:
                self._json(503, {"success": False, "code": "trigger_unavailable"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def call_local_api(token: str, operation: str, *, port: int = 8793) -> dict[str, Any]:
    """Call the fixed loopback API without placing the bearer token in argv."""

    if operation not in {"status", "run"}:
        raise ApiError("operation_not_allowed")
    method = "GET" if operation == "status" else "POST"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/{operation}", method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(MAX_STATUS_BYTES + 1)
    except Exception as error:
        raise ApiError("api_call_failed") from error
    if len(raw) > MAX_STATUS_BYTES:
        raise ApiError("api_response_too_large")
    try:
        result = json.loads(raw or b"{}")
    except (UnicodeError, ValueError) as error:
        raise ApiError("api_response_invalid") from error
    if not isinstance(result, dict):
        raise ApiError("api_response_invalid")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--token-file", type=pathlib.Path, required=True)
    parser.add_argument("--status", type=pathlib.Path, default=pathlib.Path("/var/lib/channel-monitor-patrol/status.json"))
    parser.add_argument("--trigger", type=pathlib.Path, default=pathlib.Path("/run/channel-monitor-patrol-trigger/run.request"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        address = validate_bind(args.host, args.port)
        token = read_token(args.token_file)
        server = PatrolHttpServer(address, handler_factory(token, args.status, args.trigger))
        server.serve_forever(poll_interval=0.5)
        return 0
    except Exception:
        return 2


if __name__ == "__main__":
    sys.exit(main())
