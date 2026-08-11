#!/usr/bin/env python3
"""Publish exact task-level provider evidence to the private video settlement endpoint."""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Iterable
from zoneinfo import ZoneInfo


BILLING_CONTRACT_LEGACY = "xtai-video-billing-v2"
BILLING_CONTRACT_VERSION = "xtai-video-billing-v2.1"
SUPPORTED_BILLING_CONTRACT_VERSIONS = {
    BILLING_CONTRACT_LEGACY,
    BILLING_CONTRACT_VERSION,
}
PRIVATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "video-consumption-private.json"
BEIJING = ZoneInfo("Asia/Shanghai")
MONEY_QUANTUM = Decimal("0.000001")


def _money(value: Any, *, allow_zero: bool) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (
        not number.is_finite()
        or number < 0
        or (number == 0 and not allow_zero)
        or number > Decimal("100000")
    ):
        return None
    return format(number.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING), "f")


def _observed_at(value: Any) -> str | None:
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(epoch) or epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(BEIJING).isoformat()


def _fingerprint(request: dict[str, Any]) -> str:
    material = "\0".join(
        (
            request["contract_version"],
            request["job_id"],
            request["provider_task_id"],
            request["actual_cost_status"],
            request["actual_cost_cny_exact"],
            request["evidence_source"],
            request["evidence_id"],
            request["observed_at"],
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _settlement_id(job_id: str, revision: int, fingerprint: str) -> str:
    material = "\0".join(
        ("xtai-video-settlement-v2", job_id, str(revision), fingerprint)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_settlement_requests(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build fail-closed settlement requests from exact completed provider evidence."""
    result: list[dict[str, Any]] = []
    seen_jobs: set[str] = set()
    for source in rows or []:
        row = dict(source)
        job_id = str(row.get("relay_job_id") or "").strip()
        upstream_task = str(row.get("upstream_task_id") or "").strip()
        provider_task = str(row.get("provider_task_id") or "").strip()
        status = str(row.get("actual_cost_status") or "").strip()
        if (
            not job_id.startswith("vjob_")
            or job_id in seen_jobs
            or str(row.get("status") or "") != "succeeded"
            or str(row.get("provider_state") or "") != "completed"
            or str(row.get("match_status") or "") != "exact"
            or not upstream_task
            or provider_task != upstream_task
            or status not in {"actual", "zero_verified"}
        ):
            continue
        amount = _money(
            row.get("upstream_actual_cost_cny"),
            allow_zero=status == "zero_verified",
        )
        observed = _observed_at(row.get("fetched_at"))
        source_name = str(row.get("evidence_source") or "").strip()[:120]
        if amount is None or observed is None or not source_name:
            continue
        if (status == "actual" and Decimal(amount) <= 0) or (
            status == "zero_verified" and Decimal(amount) != 0
        ):
            continue
        try:
            revision = max(1, int(row.get("settlement_revision") or 1))
        except (TypeError, ValueError):
            continue
        evidence_id = hashlib.sha256(
            "\0".join(
                (
                    str(row.get("provider_id") or ""),
                    provider_task,
                    source_name,
                    observed,
                )
            ).encode("utf-8")
        ).hexdigest()
        contract_version = str(
            row.get("billing_contract_version") or BILLING_CONTRACT_VERSION
        ).strip()
        if contract_version not in SUPPORTED_BILLING_CONTRACT_VERSIONS:
            continue
        request = {
            "contract_version": contract_version,
            "job_id": job_id,
            "revision": revision,
            "provider_task_id": provider_task,
            "actual_cost_status": status,
            "actual_cost_cny_exact": amount,
            "evidence_source": source_name,
            "evidence_id": evidence_id,
            "observed_at": observed,
        }
        request["evidence_fingerprint"] = _fingerprint(request)
        request["settlement_id"] = _settlement_id(
            job_id, revision, request["evidence_fingerprint"]
        )
        result.append(request)
        seen_jobs.add(job_id)
    return result


def build_newapi_settlement_requests(
    pending_tasks: Iterable[dict[str, Any]],
    provider_evidence: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match local pending tasks only when one exact provider task record exists."""
    evidence_by_task: dict[str, list[dict[str, Any]]] = {}
    for source in provider_evidence or []:
        row = dict(source)
        provider_task = str(row.get("provider_task_id") or "").strip()
        if (
            provider_task
            and str(row.get("state") or "") == "completed"
            and str(row.get("actual_cost_status") or "") in {"actual", "zero_verified"}
        ):
            evidence_by_task.setdefault(provider_task, []).append(row)

    result: list[dict[str, Any]] = []
    for source in pending_tasks or []:
        pending = dict(source)
        job_id = str(pending.get("job_id") or "").strip()
        provider_task = str(pending.get("provider_task_id") or "").strip()
        matches = evidence_by_task.get(provider_task) or []
        if not job_id.startswith("task_") or not provider_task or len(matches) != 1:
            continue
        row = matches[0]
        status = str(row.get("actual_cost_status") or "")
        amount = _money(row.get("actual_cost_cny"), allow_zero=status == "zero_verified")
        observed = _observed_at(row.get("fetched_at"))
        source_name = str(row.get("evidence_source") or "").strip()[:120]
        if amount is None or observed is None or not source_name:
            continue
        if (status == "actual" and Decimal(amount) <= 0) or (
            status == "zero_verified" and Decimal(amount) != 0
        ):
            continue
        try:
            revision = int(pending.get("next_revision") or 1)
        except (TypeError, ValueError):
            continue
        if revision < 1:
            continue
        evidence_id = hashlib.sha256(
            "\0".join(
                (
                    str(row.get("provider_id") or ""),
                    provider_task,
                    source_name,
                    observed,
                )
            ).encode("utf-8")
        ).hexdigest()
        contract_version = str(
            pending.get("contract_version") or BILLING_CONTRACT_VERSION
        ).strip()
        if contract_version not in SUPPORTED_BILLING_CONTRACT_VERSIONS:
            continue
        request = {
            "contract_version": contract_version,
            "job_id": job_id,
            "revision": revision,
            "provider_task_id": provider_task,
            "actual_cost_status": status,
            "actual_cost_cny_exact": amount,
            "evidence_source": source_name,
            "evidence_id": evidence_id,
            "observed_at": observed,
        }
        request["evidence_fingerprint"] = _fingerprint(request)
        request["settlement_id"] = _settlement_id(
            job_id, revision, request["evidence_fingerprint"]
        )
        result.append(request)
    return result


def _gateway_url(raw: str) -> str:
    parsed = urllib.parse.urlsplit(str(raw or "").strip())
    hostname = (parsed.hostname or "").lower()
    private_http = parsed.scheme == "http" and hostname in {
        "video-job-gateway",
        "localhost",
        "127.0.0.1",
    }
    if (
        not parsed.netloc
        or (parsed.scheme != "https" and not private_http)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("video settlement gateway URL is not approved")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    ).rstrip("/")


def _token() -> str:
    value = os.environ.get("VIDEO_SETTLEMENT_TOKEN", "").strip()
    path = pathlib.Path(
        os.environ.get(
            "VIDEO_SETTLEMENT_TOKEN_FILE",
            "/opt/ai-api-stack/channel-monitor/video-settlement-token",
        )
    )
    if not value:
        info = path.stat()
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("video settlement token file permissions are too broad")
        if info.st_size > 16_384:
            raise RuntimeError("video settlement token file is unexpectedly large")
        value = path.read_text(encoding="utf-8").strip()
    if not value or len(value) > 4096:
        raise RuntimeError("video settlement token is unavailable")
    return value


def _newapi_url(raw: str) -> str:
    parsed = urllib.parse.urlsplit(str(raw or "").strip())
    hostname = (parsed.hostname or "").lower()
    private_http = parsed.scheme == "http" and hostname in {
        "new-api",
        "localhost",
        "127.0.0.1",
    }
    if (
        not parsed.netloc
        or (parsed.scheme != "https" and not private_http)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("new-api settlement URL is not approved")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    ).rstrip("/")


def _newapi_token() -> str:
    value = os.environ.get("NEWAPI_SETTLEMENT_ACCESS_TOKEN", "").strip()
    path = pathlib.Path(
        os.environ.get(
            "NEWAPI_SETTLEMENT_ACCESS_TOKEN_FILE",
            "/opt/ai-api-stack/channel-monitor/newapi-settlement-access-token",
        )
    )
    if not value:
        info = path.stat()
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("new-api settlement token file permissions are too broad")
        if info.st_size > 16_384:
            raise RuntimeError("new-api settlement token file is unexpectedly large")
        value = path.read_text(encoding="utf-8").strip()
    if not value or len(value) > 4096:
        raise RuntimeError("new-api settlement token is unavailable")
    return value


def fetch_newapi_pending(*, base_url: str, token: str, user_id: str) -> list[dict[str, Any]]:
    endpoint = _newapi_url(base_url) + "/api/task/video-settlements/pending?limit=5000"
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={
            "Authorization": token,
            "New-Api-User": str(user_id),
            "Accept": "application/json",
            "User-Agent": "XingTuVideoSettlementPublisher/1",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read() or b"{}")
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get("success") is not True or not isinstance(data, list):
        raise RuntimeError("new-api pending settlement response is invalid")
    return [dict(row) for row in data if isinstance(row, dict)]


def publish(requests: Iterable[dict[str, Any]], *, base_url: str, token: str) -> dict[str, int]:
    endpoint = _gateway_url(base_url) + "/v1/operations/video-settlements"
    counts = {"submitted": 0, "reused": 0, "failed": 0}
    for payload in requests:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "XingTuVideoSettlementPublisher/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read() or b"{}")
            if not isinstance(result, dict) or result.get("ok") is not True:
                counts["failed"] += 1
            elif result.get("reused") is True:
                counts["reused"] += 1
            else:
                counts["submitted"] += 1
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            counts["failed"] += 1
    return counts


def publish_newapi(
    requests: Iterable[dict[str, Any]],
    *,
    base_url: str,
    token: str,
    user_id: str,
) -> dict[str, int]:
    endpoint = _newapi_url(base_url) + "/api/task/video-settlements"
    counts = {"submitted": 0, "reused": 0, "failed": 0}
    for payload in requests:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": token,
                "New-Api-User": str(user_id),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "XingTuVideoSettlementPublisher/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read() or b"{}")
            data = result.get("data") if isinstance(result, dict) else None
            if result.get("success") is not True or not isinstance(data, dict):
                counts["failed"] += 1
            elif data.get("replay") is True:
                counts["reused"] += 1
            else:
                counts["submitted"] += 1
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            counts["failed"] += 1
    return counts


def main() -> int:
    try:
        snapshot = json.loads(PRIVATE_PATH.read_text(encoding="utf-8"))
        rows = snapshot.get("reconciliation") if isinstance(snapshot, dict) else []
        requests = build_settlement_requests(rows or [])
        counts = publish(
            requests,
            base_url=os.environ.get(
                "VIDEO_SETTLEMENT_GATEWAY_URL", "http://video-job-gateway:8091"
            ),
            token=_token(),
        )
        newapi_url = os.environ.get("NEWAPI_SETTLEMENT_URL", "").strip()
        if newapi_url:
            newapi_token = _newapi_token()
            newapi_user_id = os.environ.get("NEWAPI_SETTLEMENT_USER_ID", "").strip()
            if not newapi_user_id.isdigit() or int(newapi_user_id) <= 0:
                raise RuntimeError("new-api settlement user id is unavailable")
            pending = fetch_newapi_pending(
                base_url=newapi_url,
                token=newapi_token,
                user_id=newapi_user_id,
            )
            newapi_requests = build_newapi_settlement_requests(
                pending, snapshot.get("provider_evidence") or []
            )
            newapi_counts = publish_newapi(
                newapi_requests,
                base_url=newapi_url,
                token=newapi_token,
                user_id=newapi_user_id,
            )
            for key in counts:
                counts[key] += newapi_counts[key]
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}, sort_keys=True))
        return 1
    print(json.dumps({"ok": counts["failed"] == 0, **counts}, sort_keys=True))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
