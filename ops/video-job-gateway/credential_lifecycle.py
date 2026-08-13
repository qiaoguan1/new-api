"""Safe lifecycle helpers for provider credentials that require human verification."""

from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MAX_TOKEN_BYTES = 64 * 1024


class CredentialLifecycleError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = str(code or "credential_invalid")[:120]


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    provider_id: str
    refresh_mode: str
    state: str
    ready: bool
    expires_at: int
    remaining_seconds: int


def inspect_captcha_token(
    token: str,
    *,
    provider_id: str,
    now: int,
    expected_issuer: str = "",
    expected_audience: str = "",
) -> CredentialStatus:
    value = str(token or "").strip()
    if not value or len(value.encode("utf-8")) > MAX_TOKEN_BYTES:
        raise CredentialLifecycleError("credential_token_invalid")
    parts = value.split(".")
    if len(parts) != 3:
        raise CredentialLifecycleError("credential_token_invalid")
    try:
        payload = json.loads(_urlsafe_decode(parts[1]))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CredentialLifecycleError("credential_token_invalid") from error
    if not isinstance(payload, dict):
        raise CredentialLifecycleError("credential_token_invalid")
    if expected_issuer and str(payload.get("iss") or "") != expected_issuer:
        raise CredentialLifecycleError("credential_issuer_invalid")
    if expected_audience and not _audience_matches(payload.get("aud"), expected_audience):
        raise CredentialLifecycleError("credential_audience_invalid")
    try:
        expires_at = int(payload.get("exp"))
    except (TypeError, ValueError) as error:
        raise CredentialLifecycleError("credential_expiry_invalid") from error
    remaining = expires_at - int(now)
    return CredentialStatus(
        provider_id=str(provider_id or "").strip().lower(),
        refresh_mode="captcha_bound",
        state="ready" if remaining > 0 else "expired",
        ready=remaining > 0,
        expires_at=expires_at,
        remaining_seconds=max(0, remaining),
    )


def atomic_install_captcha_token(
    path: str | Path,
    token: str,
    *,
    provider_id: str,
    now: int,
    expected_issuer: str = "",
    expected_audience: str = "",
    minimum_remaining_seconds: int = 24 * 60 * 60,
) -> CredentialStatus:
    target = _absolute_without_symlink_resolution(path)
    status = inspect_captcha_token(
        token,
        provider_id=provider_id,
        now=now,
        expected_issuer=expected_issuer,
        expected_audience=expected_audience,
    )
    if status.remaining_seconds < max(1, int(minimum_remaining_seconds)):
        raise CredentialLifecycleError("credential_lifetime_too_short")
    if target.exists():
        current = _read_private_regular_file(target)
        try:
            current_status = inspect_captcha_token(
                current,
                provider_id=provider_id,
                now=now,
                expected_issuer=expected_issuer,
                expected_audience=expected_audience,
            )
        except CredentialLifecycleError:
            current_status = None
        if current_status and current_status.ready and status.expires_at <= current_status.expires_at:
            raise CredentialLifecycleError("credential_lifetime_regression")
    _atomic_write_text(target, str(token).strip())
    return status


def warning_event(
    status: CredentialStatus,
    state: dict[str, Any],
    *,
    thresholds_days: Iterable[int] = (30, 14, 7, 3, 1),
    now: int,
) -> dict[str, Any] | None:
    provider_state = state.setdefault(status.provider_id, {})
    if not isinstance(provider_state, dict):
        provider_state = {}
        state[status.provider_id] = provider_state
    if not status.ready:
        threshold = 0
        kind = "credential_expired"
    else:
        remaining_days = status.remaining_seconds / 86400
        candidates = sorted({max(1, int(value)) for value in thresholds_days})
        crossed = [value for value in candidates if remaining_days <= value]
        if not crossed:
            provider_state.pop("last_threshold_days", None)
            return None
        threshold = min(crossed)
        kind = "credential_expiring"
    if int(provider_state.get("last_threshold_days", -1)) == threshold:
        return None
    provider_state["last_threshold_days"] = threshold
    provider_state["last_event_at"] = int(now)
    return {
        "kind": kind,
        "provider_id": status.provider_id,
        "refresh_mode": status.refresh_mode,
        "threshold_days": threshold,
        "expires_at": status.expires_at,
        "occurred_at": int(now),
    }


def atomic_write_json(path: str | Path, value: Any) -> None:
    _atomic_write_text(
        _absolute_without_symlink_resolution(path),
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )


def _read_private_regular_file(path: Path) -> str:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise CredentialLifecycleError("credential_file_unsafe")
        if info.st_size <= 0 or info.st_size > MAX_TOKEN_BYTES:
            raise CredentialLifecycleError("credential_file_unsafe")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise CredentialLifecycleError("credential_permissions_unsafe")
        return path.read_text(encoding="utf-8").strip()
    except CredentialLifecycleError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise CredentialLifecycleError("credential_file_unavailable") from error


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_owner: tuple[int, int] | None = None
    if path.exists() or path.is_symlink():
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise CredentialLifecycleError("credential_file_unsafe")
        if os.name == "posix":
            existing_owner = (current.st_uid, current.st_gid)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        if os.name == "posix":
            if existing_owner is not None:
                os.chown(path, *existing_owner)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _urlsafe_decode(value: str) -> str:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


def _absolute_without_symlink_resolution(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _audience_matches(raw: Any, expected: str) -> bool:
    if isinstance(raw, list):
        return expected in {str(value) for value in raw}
    return str(raw or "") == expected
