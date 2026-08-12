"""Read-only provider billing evidence collectors for completed video jobs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any


MONEY_QUANTUM = Decimal("0.000001")
MONEY_LIMIT = Decimal("100000")
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024
CHINA_TIMEZONE = timezone(timedelta(hours=8))


class BillingCollectionError(RuntimeError):
    """A safe, retryable provider billing collection failure."""

    def __init__(self, code: str, *, retry_after_seconds: int = 60) -> None:
        super().__init__(code)
        self.code = str(code or "provider_billing_query_failed")[:120]
        self.retry_after_seconds = max(15, min(int(retry_after_seconds), 3600))


@dataclass(frozen=True, slots=True)
class BillingRecord:
    provider_task_id: str
    actual_cost_status: str
    actual_cost_cny_exact: str
    evidence_source: str
    evidence_id: str
    observed_at: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class ToonflowBillingCollector:
    """Collect exact task-level cost from Toonflow's authenticated operation log."""

    provider_id = "toonflow"

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        token_file: str | Path | None = None,
        timeout_seconds: int = 10,
        opener: Any | None = None,
    ) -> None:
        self.endpoint = str(endpoint or "").strip()
        self._token = str(token or "").strip()
        self.token_file = Path(os.path.abspath(os.fspath(token_file))) if token_file else None
        self.timeout_seconds = max(3, min(int(timeout_seconds), 30))
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    @property
    def ready(self) -> bool:
        if not self.endpoint:
            return False
        try:
            return bool(self._token_value())
        except BillingCollectionError:
            return False

    def _token_value(self) -> str:
        value = self._token
        if self.token_file is not None:
            try:
                info = self.token_file.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise BillingCollectionError("provider_billing_credential_unsafe", retry_after_seconds=3600)
                if info.st_size <= 0 or info.st_size > MAX_CREDENTIAL_BYTES:
                    raise BillingCollectionError("provider_billing_credential_unsafe", retry_after_seconds=3600)
                if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                    raise BillingCollectionError("provider_billing_credential_permissions_unsafe", retry_after_seconds=3600)
                value = self.token_file.read_text(encoding="utf-8").strip()
            except BillingCollectionError:
                raise
            except (OSError, UnicodeDecodeError) as error:
                raise BillingCollectionError("provider_billing_credential_unavailable", retry_after_seconds=3600) from error
        if not value:
            raise BillingCollectionError("provider_billing_collector_not_configured", retry_after_seconds=3600)
        value = _safe_header_value(value)
        if value.count(".") == 2:
            try:
                payload_part = value.split(".")[1]
                padding = "=" * ((4 - len(payload_part) % 4) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_part + padding))
                expires_at = int(payload.get("exp"))
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BillingCollectionError("provider_billing_credential_invalid", retry_after_seconds=3600) from error
            if expires_at <= int(time.time()):
                raise BillingCollectionError("provider_billing_credential_expired", retry_after_seconds=3600)
        return value

    def collect(self, provider_task_id: str) -> BillingRecord:
        task_id = str(provider_task_id or "").strip()
        if not task_id or len(task_id) > 200:
            raise BillingCollectionError("provider_billing_collector_not_configured", retry_after_seconds=3600)
        token = self._token_value()
        query = urllib.parse.urlencode({"page": 1, "limit": 10, "taskICode": task_id})
        request = urllib.request.Request(
            f"{self.endpoint}?{query}",
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "XingTuVideoBillingCollector/1",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or 0)
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if not 200 <= status < 300:
                raise BillingCollectionError("provider_billing_http_error", retry_after_seconds=300)
            if len(body) > MAX_RESPONSE_BYTES:
                raise BillingCollectionError("provider_billing_response_too_large", retry_after_seconds=900)
            raw = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as error:
            if int(error.code or 0) in {401, 403}:
                raise BillingCollectionError("provider_billing_authentication_failed", retry_after_seconds=3600) from error
            if int(error.code or 0) == 429:
                raise BillingCollectionError("provider_billing_rate_limited", retry_after_seconds=120) from error
            raise BillingCollectionError("provider_billing_http_error", retry_after_seconds=300) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise BillingCollectionError("provider_billing_unavailable", retry_after_seconds=60) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900) from error
        return _parse_toonflow_record(raw, task_id)


class NewAPITaskBillingCollector:
    """Collect one terminal video task cost from an authenticated NewAPI account."""

    def __init__(
        self,
        provider_id: str,
        endpoint: str,
        *,
        credential_file: str | Path | None = None,
        authorization: str = "",
        cookie: str = "",
        new_api_user: str = "",
        rate_cny_per_usd: str = "1",
        quota_per_usd: str = "500000",
        timeout_seconds: int = 10,
        opener: Any | None = None,
    ) -> None:
        self.provider_id = str(provider_id or "").strip().lower()
        self.endpoint = str(endpoint or "").strip()
        self.credential_file = (
            Path(os.path.abspath(os.fspath(credential_file))) if credential_file else None
        )
        self._inline_headers = {
            "Authorization": str(authorization or "").strip(),
            "Cookie": str(cookie or "").strip(),
            "New-Api-User": str(new_api_user or "").strip(),
        }
        self.rate_cny_per_usd = _positive_decimal(rate_cny_per_usd, "provider_billing_rate_invalid")
        self.quota_per_usd = _positive_decimal(quota_per_usd, "provider_billing_quota_unit_invalid")
        self.timeout_seconds = max(3, min(int(timeout_seconds), 30))
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    @property
    def ready(self) -> bool:
        if not self.provider_id or not self.endpoint:
            return False
        try:
            return bool(self._auth_headers())
        except BillingCollectionError:
            return False

    def _auth_headers(self) -> dict[str, str]:
        source = dict(self._inline_headers)
        if self.credential_file is not None:
            source = _load_newapi_session(self.credential_file, self.provider_id)
        headers = {
            key: _safe_header_value(value)
            for key, value in source.items()
            if key in {"Authorization", "Cookie", "New-Api-User"} and str(value or "").strip()
        }
        if not headers or not (headers.get("Authorization") or headers.get("Cookie")):
            raise BillingCollectionError("provider_billing_collector_not_configured", retry_after_seconds=3600)
        return headers

    def collect(self, provider_task_id: str) -> BillingRecord:
        task_id = str(provider_task_id or "").strip()
        if not task_id or len(task_id) > 200:
            raise BillingCollectionError("provider_billing_task_id_invalid", retry_after_seconds=900)
        headers = {"Accept": "application/json", "User-Agent": "XingTuVideoBillingCollector/1"}
        headers.update(self._auth_headers())
        query = urllib.parse.urlencode({"p": 1, "page_size": 10, "task_id": task_id})
        request = urllib.request.Request(f"{self.endpoint}?{query}", method="GET", headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or 0)
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if not 200 <= status < 300:
                raise BillingCollectionError("provider_billing_http_error", retry_after_seconds=300)
            if len(body) > MAX_RESPONSE_BYTES:
                raise BillingCollectionError("provider_billing_response_too_large", retry_after_seconds=900)
            raw = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as error:
            if int(error.code or 0) in {401, 403}:
                raise BillingCollectionError("provider_billing_authentication_failed", retry_after_seconds=3600) from error
            if int(error.code or 0) == 429:
                raise BillingCollectionError("provider_billing_rate_limited", retry_after_seconds=120) from error
            raise BillingCollectionError("provider_billing_http_error", retry_after_seconds=300) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise BillingCollectionError("provider_billing_unavailable", retry_after_seconds=60) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900) from error
        return self._parse_newapi_record(raw, task_id)

    def _parse_newapi_record(self, raw: Any, provider_task_id: str) -> BillingRecord:
        if not isinstance(raw, dict) or raw.get("success") is not True:
            raise BillingCollectionError("provider_billing_response_rejected", retry_after_seconds=300)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        matches = [
            row
            for row in items
            if isinstance(row, dict)
            and str(row.get("task_id") or "").strip() == provider_task_id
            and "video" in str(row.get("action") or "").lower()
        ]
        if not matches:
            raise BillingCollectionError("provider_billing_record_not_ready", retry_after_seconds=60)
        if len(matches) != 1:
            raise BillingCollectionError("provider_billing_record_ambiguous", retry_after_seconds=900)
        row = matches[0]
        state = str(row.get("status") or "").strip().upper()
        if state in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
            quota = _nonnegative_decimal(row.get("quota"), "provider_billing_amount_invalid")
            amount_value = (quota / self.quota_per_usd * self.rate_cny_per_usd).quantize(
                MONEY_QUANTUM, rounding=ROUND_CEILING
            )
            if amount_value > MONEY_LIMIT:
                raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
            amount = format(amount_value, "f")
        elif state in {"FAILURE", "FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            raise BillingCollectionError("provider_billing_record_state_mismatch", retry_after_seconds=900)
        else:
            raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
        observed_at = _observed_at(row.get("finish_time") or row.get("updated_at"))
        digest = hashlib.sha256(
            "\0".join((self.provider_id, provider_task_id, amount, observed_at)).encode("utf-8")
        ).hexdigest()
        return BillingRecord(
            provider_task_id=provider_task_id,
            actual_cost_status="zero_verified" if Decimal(amount) == 0 else "actual",
            actual_cost_cny_exact=amount,
            evidence_source=f"{self.provider_id}_authenticated_video_task",
            evidence_id=f"{self.provider_id}-video-task:{digest}",
            observed_at=observed_at,
        )


def _parse_toonflow_record(raw: Any, provider_task_id: str) -> BillingRecord:
    if not isinstance(raw, dict):
        raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
    code = raw.get("code")
    if code not in {None, 0, "0", 200, "200"}:
        raise BillingCollectionError("provider_billing_response_rejected", retry_after_seconds=300)
    matches = [
        row
        for row in _operation_rows(raw)
        if str(row.get("taskICode") or "").strip() == provider_task_id
    ]
    if not matches:
        raise BillingCollectionError("provider_billing_record_not_ready", retry_after_seconds=60)
    if len(matches) != 1:
        raise BillingCollectionError("provider_billing_record_ambiguous", retry_after_seconds=900)
    row = matches[0]
    state = str(row.get("state") or "").strip().lower()
    if state not in {"2", "success", "succeeded", "completed"}:
        raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
    amount = _money_exact(row.get("price"))
    observed_at = _observed_at(row.get("completionTime"))
    digest = hashlib.sha256(
        "\0".join((provider_task_id, amount, observed_at)).encode("utf-8")
    ).hexdigest()
    return BillingRecord(
        provider_task_id=provider_task_id,
        actual_cost_status="zero_verified" if Decimal(amount) == 0 else "actual",
        actual_cost_cny_exact=amount,
        evidence_source="toonflow_web_operation_log",
        evidence_id=f"toonflow-operation-log:{digest}",
        observed_at=observed_at,
    )


def _operation_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [raw.get("rows"), raw.get("list"), raw.get("records")]
    data = raw.get("data")
    candidates.append(data)
    if isinstance(data, dict):
        candidates.extend((data.get("rows"), data.get("list"), data.get("records"), data.get("data")))
        nested = data.get("data")
        if isinstance(nested, dict):
            candidates.extend((nested.get("rows"), nested.get("list"), nested.get("records")))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)][:100]
    return []


def _money_exact(raw: Any) -> str:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900) from error
    if not value.is_finite() or value < 0 or value > MONEY_LIMIT:
        raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING), "f")


def _positive_decimal(raw: Any, code: str) -> Decimal:
    value = _nonnegative_decimal(raw, code)
    if value <= 0:
        raise BillingCollectionError(code, retry_after_seconds=3600)
    return value


def _nonnegative_decimal(raw: Any, code: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise BillingCollectionError(code, retry_after_seconds=900) from error
    if not value.is_finite() or value < 0 or value > Decimal("1000000000000000"):
        raise BillingCollectionError(code, retry_after_seconds=900)
    return value


def _load_newapi_session(path: Path, provider_id: str) -> dict[str, str]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BillingCollectionError("provider_billing_credential_unsafe", retry_after_seconds=3600)
        if info.st_size <= 0 or info.st_size > MAX_CREDENTIAL_BYTES:
            raise BillingCollectionError("provider_billing_credential_unsafe", retry_after_seconds=3600)
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise BillingCollectionError("provider_billing_credential_permissions_unsafe", retry_after_seconds=3600)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except BillingCollectionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BillingCollectionError("provider_billing_credential_unavailable", retry_after_seconds=3600) from error
    if not isinstance(raw, dict) or str(raw.get("provider_id") or "").strip().lower() != provider_id:
        raise BillingCollectionError("provider_billing_credential_identity_invalid", retry_after_seconds=3600)
    expires_at = raw.get("expires_at")
    if expires_at not in {None, ""}:
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError as error:
            raise BillingCollectionError("provider_billing_credential_expiry_invalid", retry_after_seconds=3600) from error
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise BillingCollectionError("provider_billing_credential_expiry_invalid", retry_after_seconds=3600)
        if expiry <= datetime.now(timezone.utc):
            raise BillingCollectionError("provider_billing_credential_expired", retry_after_seconds=3600)
    return {
        "Authorization": str(raw.get("authorization") or "").strip(),
        "Cookie": str(raw.get("cookie") or "").strip(),
        "New-Api-User": str(raw.get("new_api_user") or "").strip(),
    }


def _safe_header_value(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value or len(value) > 16_384 or any(character in value for character in "\r\n\0"):
        raise BillingCollectionError("provider_billing_credential_invalid", retry_after_seconds=3600)
    return value


def _observed_at(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        raise BillingCollectionError("provider_billing_completion_time_missing", retry_after_seconds=900)
    value: datetime
    try:
        epoch = Decimal(text)
    except (InvalidOperation, ValueError):
        epoch = None
    if epoch is not None and epoch.is_finite():
        if epoch <= 0:
            raise BillingCollectionError("provider_billing_completion_time_invalid", retry_after_seconds=900)
        # Toonflow currently returns JavaScript epoch milliseconds, while older
        # accounts and fixtures may expose Unix seconds. Preserve both forms.
        if epoch >= Decimal("100000000000"):
            epoch /= Decimal("1000")
        try:
            value = datetime.fromtimestamp(float(epoch), timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise BillingCollectionError("provider_billing_completion_time_invalid", retry_after_seconds=900) from error
    else:
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise BillingCollectionError("provider_billing_completion_time_invalid", retry_after_seconds=900) from error
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=CHINA_TIMEZONE)
    return value.astimezone(CHINA_TIMEZONE).isoformat(timespec="seconds")
