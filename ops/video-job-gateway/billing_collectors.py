"""Read-only provider billing evidence collectors for completed video jobs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Mapping


MONEY_QUANTUM = Decimal("0.000001")
MONEY_LIMIT = Decimal("100000")
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024
DEFAULT_MAX_MEDIA_BYTES = 512 * 1024 * 1024
CHINA_TIMEZONE = timezone(timedelta(hours=8))
PAISIO_IDENTITY_RESOLVER_VERSION = "paisio-dual-task-id-v1"
PAISIO_BILLING_TASK_PATTERN = re.compile(r"task_[A-Za-z0-9_-]{6,180}")


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
    execution_task_id: str = ""
    resolver_version: str = ""
    provider_record_id: str = ""
    provider_submit_time: int = 0
    provider_finish_time: int = 0
    media_size_bytes: int = 0
    media_sha256: str = ""


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

    def collect_failed(self, provider_task_id: str) -> BillingRecord:
        """Collect an authoritative terminal failed operation and its net price."""
        task_id = str(provider_task_id or "").strip()
        if not task_id or len(task_id) > 200:
            raise BillingCollectionError("provider_billing_task_id_invalid", retry_after_seconds=900)
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
            if not 200 <= status < 300 or len(body) > MAX_RESPONSE_BYTES:
                raise BillingCollectionError("provider_billing_http_error", retry_after_seconds=300)
            raw = json.loads(body.decode("utf-8"))
        except BillingCollectionError:
            raise
        except urllib.error.HTTPError as error:
            if int(error.code or 0) in {401, 403}:
                raise BillingCollectionError("provider_billing_authentication_failed", retry_after_seconds=3600) from error
            raise BillingCollectionError("provider_billing_http_error", retry_after_seconds=300) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise BillingCollectionError("provider_billing_unavailable", retry_after_seconds=60) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900) from error
        return _parse_toonflow_failed_record(raw, task_id)


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
        result_hosts: tuple[str, ...] = (),
        max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
        identity_max_pages: int = 10,
        identity_time_skew_seconds: int = 2,
        identity_finish_tolerance_seconds: int = 120,
        identity_resolution_enabled: bool = True,
        opener: Any | None = None,
        media_opener: Any | None = None,
        host_resolver: Any | None = None,
    ) -> None:
        self.provider_id = str(provider_id or "").strip().lower()
        self.endpoint = str(endpoint or "").strip()
        self.ledger_endpoint = (
            self.endpoint[: -len("/api/task/self")] + "/api/log/self"
            if self.endpoint.endswith("/api/task/self")
            else ""
        )
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
        self.result_hosts = tuple(
            sorted(
                {
                    str(value or "").strip().lower().rstrip(".")
                    for value in result_hosts
                    if str(value or "").strip()
                }
            )
        )
        self.max_media_bytes = max(1024, min(int(max_media_bytes), 2 * 1024 * 1024 * 1024))
        self.identity_max_pages = max(1, min(int(identity_max_pages), 20))
        self.identity_time_skew_seconds = max(0, min(int(identity_time_skew_seconds), 10))
        self.identity_finish_tolerance_seconds = max(
            5, min(int(identity_finish_tolerance_seconds), 600)
        )
        self.identity_resolution_enabled = bool(identity_resolution_enabled)
        self._media_opener = media_opener or urllib.request.build_opener(_NoRedirect())
        self._host_resolver = host_resolver or socket.getaddrinfo

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
        raw = self._request_json(
            self.endpoint,
            {"p": 1, "page_size": 10, "task_id": task_id},
            headers,
        )
        if self.provider_id == "paisio":
            # Do not touch the billing ledger until the authoritative task row
            # proves this gateway-success job is uniquely terminal and successful.
            self._terminal_task_row(raw, task_id, require_exact_filter=True)
            if not self.ledger_endpoint:
                raise BillingCollectionError(
                    "provider_billing_collector_not_configured", retry_after_seconds=3600
                )
            ledger = self._request_json(
                self.ledger_endpoint,
                {"p": 1, "page_size": 100, "request_id": task_id},
                headers,
            )
            return self._parse_paisio_record(raw, ledger, task_id)
        return self._parse_newapi_record(raw, task_id)

    def collect_failed(self, execution_task_id: str) -> BillingRecord:
        """Resolve Paisio's generation id to its billing id and net failed-task ledger."""
        if self.provider_id != "paisio":
            raise BillingCollectionError("provider_billing_failure_recovery_unsupported", retry_after_seconds=3600)
        execution_id = str(execution_task_id or "").strip()
        if not execution_id or len(execution_id) > 200:
            raise BillingCollectionError("provider_billing_task_id_invalid", retry_after_seconds=900)
        headers = {"Accept": "application/json", "User-Agent": "XingTuVideoBillingCollector/1"}
        headers.update(self._auth_headers())
        matches: list[dict[str, Any]] = []
        for page in range(1, self.identity_max_pages + 1):
            raw = self._request_json(
                self.endpoint,
                {"p": page, "page_size": 100},
                headers,
            )
            if not isinstance(raw, dict) or raw.get("success") is not True:
                raise BillingCollectionError("provider_billing_response_rejected", retry_after_seconds=300)
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            items = data.get("items") if isinstance(data.get("items"), list) else []
            for row in items:
                if not isinstance(row, dict):
                    continue
                nested = row.get("data") if isinstance(row.get("data"), dict) else {}
                if str(nested.get("task_id") or "").strip() == execution_id:
                    matches.append(row)
            try:
                total = int(data.get("total") or 0)
            except (TypeError, ValueError) as error:
                raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900) from error
            if page * 100 >= total:
                break
        if not matches:
            raise BillingCollectionError("provider_billing_record_not_ready", retry_after_seconds=60)
        if len(matches) != 1:
            raise BillingCollectionError("provider_billing_record_ambiguous", retry_after_seconds=900)
        task = matches[0]
        billing_task_id = str(task.get("task_id") or "").strip()
        state = str(task.get("status") or "").strip().upper()
        if state not in {"FAILURE", "FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            if state in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
                raise BillingCollectionError("provider_billing_recovery_state_succeeded", retry_after_seconds=900)
            raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
        if not PAISIO_BILLING_TASK_PATTERN.fullmatch(billing_task_id) or not self.ledger_endpoint:
            raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
        ledger = self._request_json(
            self.ledger_endpoint,
            {"p": 1, "page_size": 100, "request_id": billing_task_id},
            headers,
        )
        record = self._parse_paisio_failed_record(task, ledger, billing_task_id)
        return BillingRecord(
            provider_task_id=record.provider_task_id,
            actual_cost_status=record.actual_cost_status,
            actual_cost_cny_exact=record.actual_cost_cny_exact,
            evidence_source=record.evidence_source,
            evidence_id=record.evidence_id,
            observed_at=record.observed_at,
            execution_task_id=execution_id,
            resolver_version=PAISIO_IDENTITY_RESOLVER_VERSION,
            provider_record_id=str(task.get("id") or ""),
            provider_submit_time=int(task.get("submit_time") or 0),
            provider_finish_time=int(task.get("finish_time") or 0),
        )

    @property
    def identity_ready(self) -> bool:
        return self.ready and (
            self.provider_id != "paisio"
            or (bool(self.result_hosts) and self.identity_resolution_enabled)
        )

    def resolve_and_collect(
        self,
        job: Mapping[str, Any],
        *,
        allow_historical: bool = False,
    ) -> BillingRecord:
        if self.provider_id != "paisio":
            return self.collect(str(job.get("upstream_task_id") or ""))
        if not self.ready or not self.result_hosts or not self.identity_resolution_enabled:
            raise BillingCollectionError(
                "provider_billing_identity_resolver_not_configured",
                retry_after_seconds=3600,
            )
        execution_task_id = str(job.get("upstream_task_id") or "").strip()
        if not execution_task_id or len(execution_task_id) > 200:
            raise BillingCollectionError("provider_billing_task_id_invalid", retry_after_seconds=900)
        if (
            str(job.get("provider_id") or "").strip().lower() != "paisio"
            or str(job.get("status") or "") != "succeeded"
            or str(job.get("billing_status") or "") != "settlement_pending"
        ):
            raise BillingCollectionError("provider_billing_identity_job_invalid", retry_after_seconds=900)
        try:
            submit_started = int(job.get("submit_started_at") or 0)
            submit_confirmed = int(job.get("submit_confirmed_at") or 0)
            gateway_finished = int(job.get("finished_at") or 0)
        except (TypeError, ValueError) as error:
            raise BillingCollectionError(
                "provider_billing_identity_window_invalid", retry_after_seconds=900
            ) from error
        if not submit_started or not submit_confirmed:
            if not allow_historical:
                raise BillingCollectionError(
                    "provider_billing_identity_window_missing", retry_after_seconds=3600
                )
            try:
                created_at = int(job.get("created_at") or 0)
            except (TypeError, ValueError) as error:
                raise BillingCollectionError(
                    "provider_billing_identity_window_invalid", retry_after_seconds=900
                ) from error
            if created_at <= 0:
                raise BillingCollectionError(
                    "provider_billing_identity_window_missing", retry_after_seconds=3600
                )
            submit_started = created_at - 5
            submit_confirmed = created_at + 120
        if (
            submit_started <= 0
            or submit_confirmed < submit_started
            or submit_confirmed - submit_started > 600
            or gateway_finished < submit_started
        ):
            raise BillingCollectionError(
                "provider_billing_identity_window_invalid", retry_after_seconds=900
            )
        try:
            result = json.loads(str(job.get("result_json") or ""))
        except json.JSONDecodeError as error:
            raise BillingCollectionError(
                "provider_billing_identity_result_invalid", retry_after_seconds=900
            ) from error
        if not isinstance(result, dict) or bool(result.get("requires_auth")):
            raise BillingCollectionError(
                "provider_billing_identity_result_invalid", retry_after_seconds=900
            )
        gateway_result_url = str(result.get("source_url") or "").strip()
        if not gateway_result_url:
            raise BillingCollectionError(
                "provider_billing_identity_result_invalid", retry_after_seconds=900
            )

        lower = submit_started - self.identity_time_skew_seconds
        upper = submit_confirmed + self.identity_time_skew_seconds
        candidates = self._paisio_identity_candidates(
            lower_submit_time=lower,
            upper_submit_time=upper,
            gateway_finished_at=gateway_finished,
        )
        if not candidates:
            raise BillingCollectionError("provider_billing_record_not_ready", retry_after_seconds=60)
        if len(candidates) > 10:
            raise BillingCollectionError("provider_billing_record_ambiguous", retry_after_seconds=900)

        gateway_size, gateway_sha256 = self._stream_media_identity(gateway_result_url)
        matches: list[tuple[dict[str, Any], int, str]] = []
        for row in candidates:
            provider_url = _paisio_video_url(row)
            provider_size, provider_sha256 = self._stream_media_identity(provider_url)
            if provider_size == gateway_size and hmac.compare_digest(
                provider_sha256, gateway_sha256
            ):
                matches.append((row, provider_size, provider_sha256))
        if not matches:
            raise BillingCollectionError(
                "provider_billing_identity_media_mismatch", retry_after_seconds=900
            )
        if len(matches) != 1:
            raise BillingCollectionError("provider_billing_record_ambiguous", retry_after_seconds=900)

        row, media_size, media_sha256 = matches[0]
        billing_task_id = str(row.get("task_id") or "").strip()
        headers = {"Accept": "application/json", "User-Agent": "XingTuVideoBillingCollector/1"}
        headers.update(self._auth_headers())
        if not self.ledger_endpoint:
            raise BillingCollectionError(
                "provider_billing_collector_not_configured", retry_after_seconds=3600
            )
        ledger = self._request_json(
            self.ledger_endpoint,
            {"p": 1, "page_size": 100, "request_id": billing_task_id},
            headers,
        )
        task_raw = {"success": True, "data": {"total": 1, "items": [row]}}
        record = self._parse_paisio_record(task_raw, ledger, billing_task_id)
        return BillingRecord(
            provider_task_id=record.provider_task_id,
            actual_cost_status=record.actual_cost_status,
            actual_cost_cny_exact=record.actual_cost_cny_exact,
            evidence_source=record.evidence_source,
            evidence_id=record.evidence_id,
            observed_at=record.observed_at,
            execution_task_id=execution_task_id,
            resolver_version=PAISIO_IDENTITY_RESOLVER_VERSION,
            provider_record_id=str(row.get("id") or "").strip(),
            provider_submit_time=int(row.get("submit_time") or 0),
            provider_finish_time=int(row.get("finish_time") or 0),
            media_size_bytes=media_size,
            media_sha256=media_sha256,
        )

    def _paisio_identity_candidates(
        self,
        *,
        lower_submit_time: int,
        upper_submit_time: int,
        gateway_finished_at: int,
    ) -> list[dict[str, Any]]:
        headers = {"Accept": "application/json", "User-Agent": "XingTuVideoBillingCollector/1"}
        headers.update(self._auth_headers())
        candidates: list[dict[str, Any]] = []
        expected_total: int | None = None
        total_pages = 1
        for page in range(1, self.identity_max_pages + 1):
            raw = self._request_json(
                self.endpoint,
                {"p": page, "page_size": 100},
                headers,
            )
            if not isinstance(raw, dict) or raw.get("success") is not True:
                raise BillingCollectionError(
                    "provider_billing_response_rejected", retry_after_seconds=300
                )
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            items = data.get("items") if isinstance(data.get("items"), list) else None
            try:
                total = int(data.get("total"))
                response_page = int(data.get("page"))
                page_size = int(data.get("page_size"))
            except (TypeError, ValueError) as error:
                raise BillingCollectionError(
                    "provider_billing_ledger_incomplete", retry_after_seconds=900
                ) from error
            if (
                items is None
                or isinstance(data.get("total"), bool)
                or total < 0
                or response_page != page
                or page_size != 100
                or len(items) > 100
            ):
                raise BillingCollectionError(
                    "provider_billing_ledger_incomplete", retry_after_seconds=900
                )
            if expected_total is None:
                expected_total = total
                total_pages = max(1, (total + 99) // 100)
                if total_pages > self.identity_max_pages:
                    raise BillingCollectionError(
                        "provider_billing_identity_page_limit", retry_after_seconds=900
                    )
            elif total != expected_total:
                raise BillingCollectionError(
                    "provider_billing_identity_snapshot_changed", retry_after_seconds=60
                )
            for row in items:
                if not isinstance(row, dict):
                    raise BillingCollectionError(
                        "provider_billing_response_invalid", retry_after_seconds=900
                    )
                task_id = str(row.get("task_id") or "").strip()
                action = str(row.get("action") or "").strip()
                state = str(row.get("status") or "").strip()
                try:
                    submit_time = int(row.get("submit_time") or 0)
                    finish_time = int(row.get("finish_time") or 0)
                except (TypeError, ValueError) as error:
                    raise BillingCollectionError(
                        "provider_billing_response_invalid", retry_after_seconds=900
                    ) from error
                if (
                    action == "videoGenerate"
                    and state == "SUCCESS"
                    and PAISIO_BILLING_TASK_PATTERN.fullmatch(task_id)
                    and lower_submit_time <= submit_time <= upper_submit_time
                    and finish_time >= submit_time
                    and finish_time <= gateway_finished_at + self.identity_time_skew_seconds
                    and gateway_finished_at - finish_time
                    <= self.identity_finish_tolerance_seconds
                ):
                    candidates.append(row)
            if page >= total_pages:
                break
        return candidates

    def _stream_media_identity(self, url: str) -> tuple[int, str]:
        _validate_media_url(url, self.result_hosts, self._host_resolver)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "video/mp4,application/octet-stream",
                "User-Agent": "XingTuVideoIdentityResolver/1",
            },
        )
        try:
            response = self._media_opener.open(request, timeout=self.timeout_seconds)
            try:
                status = int(getattr(response, "status", 0) or 0)
                if not 200 <= status < 300:
                    raise BillingCollectionError(
                        "provider_billing_identity_media_http_error", retry_after_seconds=300
                    )
                try:
                    declared = int(response.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    declared = 0
                if declared > self.max_media_bytes:
                    raise BillingCollectionError(
                        "provider_billing_identity_media_too_large", retry_after_seconds=900
                    )
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_media_bytes:
                        raise BillingCollectionError(
                            "provider_billing_identity_media_too_large", retry_after_seconds=900
                        )
                    digest.update(chunk)
                if size <= 0 or (declared and declared != size):
                    raise BillingCollectionError(
                        "provider_billing_identity_media_incomplete", retry_after_seconds=300
                    )
                return size, digest.hexdigest()
            finally:
                response.close()
        except BillingCollectionError:
            raise
        except urllib.error.HTTPError as error:
            if int(error.code or 0) in {301, 302, 303, 307, 308}:
                raise BillingCollectionError(
                    "provider_billing_identity_media_redirect", retry_after_seconds=900
                ) from error
            raise BillingCollectionError(
                "provider_billing_identity_media_http_error", retry_after_seconds=300
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError, socket.timeout, ssl.SSLError) as error:
            raise BillingCollectionError(
                "provider_billing_identity_media_unavailable", retry_after_seconds=60
            ) from error

    def _request_json(
        self,
        endpoint: str,
        query_values: dict[str, Any],
        headers: dict[str, str],
    ) -> Any:
        query = urllib.parse.urlencode(query_values)
        request = urllib.request.Request(f"{endpoint}?{query}", method="GET", headers=headers)
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
        return raw

    def _terminal_task_row(
        self,
        raw: Any,
        provider_task_id: str,
        *,
        require_exact_filter: bool = False,
    ) -> dict[str, Any]:
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
        if require_exact_filter:
            try:
                total = int(data.get("total"))
            except (TypeError, ValueError) as error:
                raise BillingCollectionError(
                    "provider_billing_ledger_incomplete", retry_after_seconds=900
                ) from error
            if isinstance(data.get("total"), bool) or total != 1 or len(items) != 1:
                raise BillingCollectionError(
                    "provider_billing_ledger_incomplete", retry_after_seconds=900
                )
        row = matches[0]
        state = str(row.get("status") or "").strip().upper()
        if state in {"FAILURE", "FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            raise BillingCollectionError("provider_billing_record_state_mismatch", retry_after_seconds=900)
        if state not in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
            raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
        return row

    def _parse_paisio_record(
        self,
        task_raw: Any,
        ledger_raw: Any,
        provider_task_id: str,
    ) -> BillingRecord:
        """Use Paisio's request ledger; task `quota` is a count, never money."""
        task = self._terminal_task_row(task_raw, provider_task_id, require_exact_filter=True)
        if not isinstance(ledger_raw, dict) or ledger_raw.get("success") is not True:
            raise BillingCollectionError("provider_billing_response_rejected", retry_after_seconds=300)
        data = ledger_raw.get("data") if isinstance(ledger_raw.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else None
        if items is None:
            raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
        try:
            total = int(data.get("total"))
        except (TypeError, ValueError) as error:
            raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900) from error
        if total != len(items) or total <= 0 or total > 100:
            raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900)

        quotas: list[Decimal] = []
        fingerprints: list[str] = []
        completed = 0
        refunded = 0
        for row in items:
            try:
                row_type = int(row.get("type") or 0) if isinstance(row, dict) else 0
            except (TypeError, ValueError) as error:
                raise BillingCollectionError(
                    "provider_billing_response_invalid", retry_after_seconds=900
                ) from error
            if (
                not isinstance(row, dict)
                or row_type != 2
                or str(row.get("request_id") or "").strip() != provider_task_id
            ):
                raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900)
            quota = _signed_decimal(row.get("quota"), "provider_billing_amount_invalid")
            try:
                other = json.loads(row.get("other") or "{}")
            except (TypeError, json.JSONDecodeError) as error:
                raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900) from error
            if not isinstance(other, dict):
                raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
            billing_type = str(other.get("billing_type") or "").strip()
            if billing_type in {"per_sec", "per_call"}:
                if quota <= 0:
                    raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
            elif billing_type == "completed":
                if quota != 0:
                    raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
                completed += 1
            elif billing_type == "generation_failed_refund":
                if quota >= 0:
                    raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
                refunded += 1
            else:
                raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
            quotas.append(quota)
            fingerprints.append(f"{row.get('id')}:{billing_type}:{quota}")

        if refunded:
            raise BillingCollectionError("provider_billing_record_state_mismatch", retry_after_seconds=900)
        if completed != 1:
            raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
        net_quota = sum(quotas, Decimal("0"))
        if net_quota < 0:
            raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
        amount_value = (net_quota / self.quota_per_usd * self.rate_cny_per_usd).quantize(
            MONEY_QUANTUM, rounding=ROUND_CEILING
        )
        if amount_value > MONEY_LIMIT:
            raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
        amount = format(amount_value, "f")
        observed_at = _observed_at(task.get("finish_time") or task.get("updated_at"))
        digest = hashlib.sha256(
            "\0".join(
                (self.provider_id, provider_task_id, amount, observed_at, *sorted(fingerprints))
            ).encode("utf-8")
        ).hexdigest()
        return BillingRecord(
            provider_task_id=provider_task_id,
            actual_cost_status="zero_verified" if amount_value == 0 else "actual",
            actual_cost_cny_exact=amount,
            evidence_source="paisio_authenticated_request_ledger",
            evidence_id=f"paisio-request-ledger:{digest}",
            observed_at=observed_at,
        )

    def _parse_paisio_failed_record(
        self,
        task: Mapping[str, Any],
        ledger_raw: Any,
        provider_task_id: str,
    ) -> BillingRecord:
        if not isinstance(ledger_raw, dict) or ledger_raw.get("success") is not True:
            raise BillingCollectionError("provider_billing_response_rejected", retry_after_seconds=300)
        data = ledger_raw.get("data") if isinstance(ledger_raw.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else None
        if items is None:
            raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
        try:
            total = int(data.get("total"))
        except (TypeError, ValueError) as error:
            raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900) from error
        if total != len(items) or total <= 0 or total > 100:
            raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900)
        quotas: list[Decimal] = []
        fingerprints: list[str] = []
        refund_rows = 0
        charge_rows = 0
        for row in items:
            if not isinstance(row, dict) or str(row.get("request_id") or "").strip() != provider_task_id:
                raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900)
            try:
                row_type = int(row.get("type") or 0)
                other = json.loads(row.get("other") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900) from error
            if row_type != 2 or not isinstance(other, dict):
                raise BillingCollectionError("provider_billing_ledger_incomplete", retry_after_seconds=900)
            quota = _signed_decimal(row.get("quota"), "provider_billing_amount_invalid")
            billing_type = str(other.get("billing_type") or "").strip()
            if billing_type in {"per_sec", "per_call"} and quota > 0:
                charge_rows += 1
            elif billing_type == "generation_failed_refund" and quota < 0:
                refund_rows += 1
            else:
                raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
            quotas.append(quota)
            fingerprints.append(f"{row.get('id')}:{billing_type}:{quota}")
        if charge_rows < 1 or refund_rows < 1:
            raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
        net_quota = sum(quotas, Decimal("0"))
        if net_quota < 0:
            raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
        amount_value = (net_quota / self.quota_per_usd * self.rate_cny_per_usd).quantize(
            MONEY_QUANTUM, rounding=ROUND_CEILING
        )
        amount = format(amount_value, "f")
        observed_at = _observed_at(task.get("finish_time") or task.get("updated_at"))
        digest = hashlib.sha256(
            "\0".join((self.provider_id, provider_task_id, "failed", amount, observed_at, *sorted(fingerprints))).encode("utf-8")
        ).hexdigest()
        return BillingRecord(
            provider_task_id=provider_task_id,
            actual_cost_status="zero_verified" if amount_value == 0 else "actual",
            actual_cost_cny_exact=amount,
            evidence_source="paisio_authenticated_failed_request_ledger",
            evidence_id=f"paisio-failed-request-ledger:{digest}",
            observed_at=observed_at,
        )

    def _parse_newapi_record(self, raw: Any, provider_task_id: str) -> BillingRecord:
        row = self._terminal_task_row(raw, provider_task_id)
        quota = _nonnegative_decimal(row.get("quota"), "provider_billing_amount_invalid")
        amount_value = (quota / self.quota_per_usd * self.rate_cny_per_usd).quantize(
            MONEY_QUANTUM, rounding=ROUND_CEILING
        )
        if amount_value > MONEY_LIMIT:
            raise BillingCollectionError("provider_billing_amount_invalid", retry_after_seconds=900)
        amount = format(amount_value, "f")
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


def _paisio_video_url(row: Mapping[str, Any]) -> str:
    values: list[Any] = [
        row.get("result_url"),
        row.get("video_url"),
        row.get("output_url"),
    ]
    for key in ("data", "result", "output"):
        nested = row.get(key)
        if isinstance(nested, dict):
            values.extend(
                (nested.get("video_url"), nested.get("result_url"), nested.get("output_url"))
            )
    urls = {str(value or "").strip() for value in values if str(value or "").strip()}
    if len(urls) != 1:
        raise BillingCollectionError(
            "provider_billing_identity_result_invalid", retry_after_seconds=900
        )
    return urls.pop()


def _validate_media_url(url: str, allowed_hosts: tuple[str, ...], resolver: Any) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as error:
        raise BillingCollectionError(
            "provider_billing_identity_result_url_unsafe", retry_after_seconds=900
        ) from error
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port not in {None, 443}
        or len(str(url or "")) > 4096
    ):
        raise BillingCollectionError(
            "provider_billing_identity_result_url_unsafe", retry_after_seconds=900
        )
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
        raise BillingCollectionError(
            "provider_billing_identity_result_host_unsafe", retry_after_seconds=900
        )
    try:
        addresses = {
            item[4][0]
            for item in resolver(host, 443, type=socket.SOCK_STREAM)
        }
    except (OSError, socket.gaierror) as error:
        raise BillingCollectionError(
            "provider_billing_identity_result_host_unresolved", retry_after_seconds=60
        ) from error
    if not addresses:
        raise BillingCollectionError(
            "provider_billing_identity_result_host_unresolved", retry_after_seconds=60
        )
    for value in addresses:
        try:
            address = ipaddress.ip_address(str(value).split("%")[0])
        except ValueError as error:
            raise BillingCollectionError(
                "provider_billing_identity_result_host_unsafe", retry_after_seconds=900
            ) from error
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
        ):
            raise BillingCollectionError(
                "provider_billing_identity_result_host_unsafe", retry_after_seconds=900
            )
    return host


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


def _parse_toonflow_failed_record(raw: Any, provider_task_id: str) -> BillingRecord:
    if not isinstance(raw, dict):
        raise BillingCollectionError("provider_billing_response_invalid", retry_after_seconds=900)
    code = raw.get("code")
    if code not in {None, 0, "0", 200, "200"}:
        raise BillingCollectionError("provider_billing_response_rejected", retry_after_seconds=300)
    matches = [
        row for row in _operation_rows(raw)
        if str(row.get("taskICode") or "").strip() == provider_task_id
    ]
    if not matches:
        raise BillingCollectionError("provider_billing_record_not_ready", retry_after_seconds=60)
    if len(matches) != 1:
        raise BillingCollectionError("provider_billing_record_ambiguous", retry_after_seconds=900)
    row = matches[0]
    state = str(row.get("state") or "").strip().lower()
    if state not in {"-1", "failed", "failure", "error", "cancelled", "canceled"}:
        if state in {"2", "success", "succeeded", "completed"}:
            raise BillingCollectionError("provider_billing_recovery_state_succeeded", retry_after_seconds=900)
        raise BillingCollectionError("provider_billing_record_not_final", retry_after_seconds=60)
    amount = _money_exact(row.get("price"))
    observed_at = _observed_at(row.get("completionTime") or row.get("updateTime"))
    digest = hashlib.sha256(
        "\0".join((provider_task_id, "failed", amount, observed_at)).encode("utf-8")
    ).hexdigest()
    return BillingRecord(
        provider_task_id=provider_task_id,
        actual_cost_status="zero_verified" if Decimal(amount) == 0 else "actual",
        actual_cost_cny_exact=amount,
        evidence_source="toonflow_web_failed_operation_log",
        evidence_id=f"toonflow-failed-operation-log:{digest}",
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


def _signed_decimal(raw: Any, code: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise BillingCollectionError(code, retry_after_seconds=900) from error
    if not value.is_finite() or abs(value) > Decimal("1000000000000000"):
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
