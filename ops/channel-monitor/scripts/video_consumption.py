"""Evidence-first video usage parsing, reconciliation, and monitor projections."""

from __future__ import annotations

import json
import math
import pathlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
TERMINAL_STATES = {"completed", "failed", "refunded"}


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _epoch(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        if abs(number) > 100_000_000_000:
            number /= 1000
        return int(number)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return _epoch(float(text))
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING)
    return int(parsed.timestamp())


def _beijing_day(epoch: int | None) -> str:
    if epoch is None:
        return ""
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(BEIJING).date().isoformat()


def _iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(BEIJING).isoformat()


def _toonflow_rows(payload: Any) -> list[dict[str, Any]]:
    current = payload
    if isinstance(current, dict) and "data" in current:
        current = current.get("data")
    if isinstance(current, list):
        return [row for row in current if isinstance(row, dict)]
    if not isinstance(current, dict):
        return []
    for key in ("list", "rows", "records", "items"):
        value = current.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _stable_video_model(raw_model: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(raw_model or "").lower()).strip("-")
    if not text:
        return "unknown"
    if "mini" in text:
        return "seedance-2.0-mini"
    if "fast" in text:
        return "seedance-2.0-fast"
    if "seedance" in text or text.startswith("sd2") or "2-0" in text:
        return "seedance-2.0-full"
    return text


def _resolution(raw_model: Any) -> str:
    match = re.search(r"(?<!\d)(480|720|1080)p?(?!\d)", str(raw_model or "").lower())
    return f"{match.group(1)}p" if match else ""


def parse_toonflow_operation_rows(
    payload: Any,
    day: str,
    *,
    rate: float = 1.0,
    fetched_at: int | None = None,
) -> list[dict[str, Any]]:
    """Parse one complete Toonflow operation-log payload for a Beijing business day.

    The caller is responsible for proving pagination completeness. Completed records use the
    authenticated operation price. Failed records are verified zero-cost records. In-progress or
    identifier-less rows never become actual cost evidence.
    """
    exchange_rate = _finite_float(rate)
    if exchange_rate is None or exchange_rate <= 0:
        raise ValueError("Toonflow exchange rate must be positive")
    fetched = int(fetched_at or datetime.now(timezone.utc).timestamp())
    result: list[dict[str, Any]] = []
    for source in _toonflow_rows(payload):
        created_epoch = _epoch(source.get("creationTime") or source.get("createdAt"))
        completed_epoch = _epoch(source.get("completionTime") or source.get("completedAt"))
        event_epoch = created_epoch if created_epoch is not None else completed_epoch
        if _beijing_day(event_epoch) != day:
            continue
        raw_state = source.get("state")
        state = {2: "completed", -1: "failed", 1: "running"}.get(raw_state, "unknown")
        if state == "unknown":
            state = {"2": "completed", "-1": "failed", "1": "running"}.get(
                str(raw_state), "unknown"
            )
        task_id = str(source.get("taskICode") or source.get("taskId") or "").strip()
        price = _finite_float(source.get("price"))
        if task_id and state == "completed" and price is not None and price >= 0:
            actual_cost = round(price * exchange_rate, 8)
            cost_status = "actual" if actual_cost > 0 else "zero_verified"
        elif task_id and state in {"failed", "refunded"}:
            actual_cost = 0.0
            cost_status = "zero_verified"
        else:
            actual_cost = None
            cost_status = "unknown"
        raw_model = str(source.get("modelName") or source.get("model") or "").strip()
        result.append(
            {
                "provider_id": "toonflow",
                "provider_task_id": task_id,
                "raw_model": raw_model,
                "stable_model": _stable_video_model(raw_model),
                "resolution": _resolution(raw_model),
                "state": state,
                "created_at": _iso(created_epoch),
                "completed_at": _iso(completed_epoch),
                "created_at_epoch": created_epoch or 0,
                "completed_at_epoch": completed_epoch or 0,
                "actual_cost_cny": actual_cost,
                "actual_cost_status": cost_status,
                "evidence_source": "toonflow_web_operation_log",
                "fetched_at": fetched,
            }
        )
    return dedupe_provider_usage(result)


def parse_newapi_video_task_rows(
    payload: Any,
    day: str,
    *,
    provider_id: str,
    rate: float = 1.0,
    quota_per_usd: float = 500_000.0,
    fetched_at: int | None = None,
) -> list[dict[str, Any]]:
    """Parse authenticated NewAPI video task rows into task-level cost evidence."""
    exchange_rate = _finite_float(rate)
    quota_divisor = _finite_float(quota_per_usd)
    provider = str(provider_id or "").strip().lower()
    if not provider or exchange_rate is None or exchange_rate <= 0:
        raise ValueError("provider and positive exchange rate are required")
    if quota_divisor is None or quota_divisor <= 0:
        raise ValueError("quota divisor must be positive")
    current = payload.get("data") if isinstance(payload, dict) else None
    items = current.get("items") if isinstance(current, dict) else None
    fetched = int(fetched_at or datetime.now(timezone.utc).timestamp())
    result: list[dict[str, Any]] = []
    for source in items or []:
        if not isinstance(source, dict):
            continue
        action = re.sub(r"[^a-z]", "", str(source.get("action") or "").lower())
        if "video" not in action:
            continue
        created_epoch = _epoch(source.get("created_at") or source.get("submit_time"))
        if _beijing_day(created_epoch) != day:
            continue
        raw_status = str(source.get("status") or "").strip().upper()
        if raw_status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
            state = "completed"
        elif raw_status in {"FAILURE", "FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            state = "failed"
        elif raw_status in {"PENDING", "SUBMITTED", "PROCESSING", "IN_PROGRESS", "RUNNING"}:
            state = "running"
        else:
            state = "unknown"
        task_id = str(source.get("task_id") or "").strip()
        quota = _finite_float(source.get("quota"))
        if task_id and state == "completed" and quota is not None and quota >= 0:
            actual_cost = round(quota / quota_divisor * exchange_rate, 8)
            cost_status = "actual" if actual_cost > 0 else "zero_verified"
        elif task_id and state == "failed":
            actual_cost = 0.0
            cost_status = "zero_verified"
        else:
            actual_cost = None
            cost_status = "unknown"
        completed_epoch = _epoch(source.get("finish_time") or source.get("updated_at"))
        result.append(
            {
                "provider_id": provider,
                "provider_task_id": task_id,
                "raw_model": "",
                "stable_model": "",
                "resolution": "",
                "state": state,
                "created_at": _iso(created_epoch),
                "completed_at": _iso(completed_epoch),
                "created_at_epoch": created_epoch or 0,
                "completed_at_epoch": completed_epoch or 0,
                "actual_cost_cny": actual_cost,
                "actual_cost_status": cost_status,
                "evidence_source": "newapi_authenticated_video_task",
                "fetched_at": fetched,
            }
        )
    return dedupe_provider_usage(result)


def dedupe_provider_usage(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate provider records by provider and task ID, preferring latest terminal evidence."""
    selected: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    unidentified: list[dict[str, Any]] = []
    for position, source in enumerate(rows or []):
        row = dict(source)
        provider = str(row.get("provider_id") or "").strip().lower()
        task_id = str(row.get("provider_task_id") or "").strip()
        if not provider or not task_id:
            row["actual_cost_cny"] = None
            row["actual_cost_status"] = "unknown"
            unidentified.append(row)
            continue
        terminal = 1 if str(row.get("state") or "") in TERMINAL_STATES else 0
        timestamp = int(row.get("completed_at_epoch") or row.get("created_at_epoch") or 0)
        score = terminal * 10**15 + timestamp * 1000 + position
        key = (provider, task_id)
        if key not in selected or score >= selected[key][0]:
            selected[key] = (score, row)
    identified = [item[1] for item in selected.values()]
    return sorted(
        identified + unidentified,
        key=lambda row: (
            int(row.get("created_at_epoch") or 0),
            str(row.get("provider_id") or ""),
            str(row.get("provider_task_id") or ""),
        ),
    )


def _money(value: Any) -> float | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0 or number > Decimal("1000000"):
        return None
    return float(number)


def gateway_rows_from_sqlite(path: str | pathlib.Path, day: str) -> list[dict[str, Any]]:
    """Read relay jobs for one Beijing day from a SQLite database in read-only mode."""
    try:
        start = datetime.fromisoformat(day).replace(tzinfo=BEIJING)
    except ValueError as exc:
        raise ValueError("gateway business day must use YYYY-MM-DD") from exc
    start_epoch = int(start.timestamp())
    end_epoch = int((start + timedelta(days=1)).timestamp())
    database = pathlib.Path(path).resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """select job_id,provider_id,upstream_task_id,stable_model,status,payload_json,
                      created_at,updated_at,finished_at
               from video_jobs where created_at>=? and created_at<? order by created_at,job_id""",
            (start_epoch, end_epoch),
        ).fetchall()
    finally:
        connection.close()
    result: list[dict[str, Any]] = []
    for source in rows:
        created = int(source["created_at"] or 0)
        try:
            payload = json.loads(source["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        route = payload.get("_route") if isinstance(payload.get("_route"), dict) else {}
        quote = payload.get("_relay_price") if isinstance(payload.get("_relay_price"), dict) else {}
        result.append(
            {
                "relay_job_id": str(source["job_id"] or ""),
                "provider_id": str(source["provider_id"] or "").strip().lower(),
                "upstream_task_id": str(source["upstream_task_id"] or "").strip(),
                "stable_model": str(source["stable_model"] or "").strip(),
                "resolution": str(payload.get("resolution") or route.get("resolution") or "").lower(),
                "status": str(source["status"] or "unknown"),
                "created_at_epoch": created,
                "updated_at_epoch": int(source["updated_at"] or 0),
                "finished_at_epoch": int(source["finished_at"] or 0),
                "relay_sale_cny": _money(quote.get("amount_cny_exact")),
                "relay_price_source": str(quote.get("price_source") or ""),
            }
        )
    return result


def reconcile_video_usage(
    gateway_jobs: Iterable[dict[str, Any]],
    provider_records: Iterable[dict[str, Any]],
    *,
    match_window_seconds: int = 600,
) -> list[dict[str, Any]]:
    """Reconcile relay jobs with deduplicated provider evidence without guessing ambiguity."""
    evidence = dedupe_provider_usage(provider_records)
    exact = {
        (str(row.get("provider_id") or ""), str(row.get("provider_task_id") or "")): row
        for row in evidence
        if row.get("provider_id") and row.get("provider_task_id")
    }
    used: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for source in gateway_jobs or []:
        job = dict(source)
        provider = str(job.get("provider_id") or "").strip().lower()
        upstream_task = str(job.get("upstream_task_id") or "").strip()
        match = exact.get((provider, upstream_task)) if upstream_task else None
        match_status = "exact" if match else "unmatched"
        if match:
            used.add((provider, upstream_task))
        elif not upstream_task:
            candidates = []
            job_epoch = int(job.get("created_at_epoch") or 0)
            for candidate in evidence:
                key = (provider, str(candidate.get("provider_task_id") or ""))
                if key in used or str(candidate.get("provider_id") or "") != provider:
                    continue
                if str(candidate.get("stable_model") or "") != str(job.get("stable_model") or ""):
                    continue
                job_resolution = str(job.get("resolution") or "")
                candidate_resolution = str(candidate.get("resolution") or "")
                if job_resolution and candidate_resolution and job_resolution != candidate_resolution:
                    continue
                candidate_epoch = int(candidate.get("created_at_epoch") or 0)
                if job_epoch and candidate_epoch and abs(job_epoch - candidate_epoch) <= match_window_seconds:
                    candidates.append(candidate)
            if len(candidates) == 1:
                match = candidates[0]
                match_status = "inferred_unique"
                used.add((provider, str(match.get("provider_task_id") or "")))
            elif len(candidates) > 1:
                match_status = "ambiguous"
        actual_status = str((match or {}).get("actual_cost_status") or "unknown")
        actual_cost = (match or {}).get("actual_cost_cny")
        if actual_status not in {"actual", "zero_verified"}:
            actual_status = "unknown"
            actual_cost = None
        result.append(
            {
                **job,
                "provider_task_id": str((match or {}).get("provider_task_id") or ""),
                "match_status": match_status,
                "provider_state": str((match or {}).get("state") or "unknown"),
                "upstream_actual_cost_cny": actual_cost,
                "actual_cost_status": actual_status,
                "evidence_source": (match or {}).get("evidence_source"),
                "fetched_at": (match or {}).get("fetched_at"),
            }
        )
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def build_monitor_snapshots(
    day: str,
    reconciled_rows: Iterable[dict[str, Any]],
    *,
    generated_at: str,
    collection_entries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build credential-free private and provider-neutral public monitor snapshots."""
    rows = [dict(row) for row in reconciled_rows or []]
    providers: dict[str, dict[str, Any]] = {}
    models: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        provider = str(row.get("provider_id") or "unknown")
        provider_row = providers.setdefault(
            provider,
            {
                "provider_id": provider,
                "task_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "actual_cost_evidence_count": 0,
                "upstream_actual_cost_cny": 0.0,
                "relay_sale_cny": 0.0,
                "last_fetch_at": None,
            },
        )
        provider_row["task_count"] += 1
        if row.get("status") == "succeeded":
            provider_row["success_count"] += 1
        elif row.get("status") == "failed":
            provider_row["failed_count"] += 1
        if row.get("actual_cost_status") in {"actual", "zero_verified"}:
            provider_row["actual_cost_evidence_count"] += 1
            provider_row["upstream_actual_cost_cny"] += float(
                row.get("upstream_actual_cost_cny") or 0
            )
        provider_row["relay_sale_cny"] += float(row.get("relay_sale_cny") or 0)
        fetched = row.get("fetched_at")
        if fetched is not None and (
            provider_row["last_fetch_at"] is None or fetched > provider_row["last_fetch_at"]
        ):
            provider_row["last_fetch_at"] = fetched

        stable = str(row.get("stable_model") or "unknown")
        resolution = str(row.get("resolution") or "")
        model_row = models.setdefault(
            (stable, resolution),
            {
                "model": stable,
                "resolution": resolution,
                "available": False,
                "task_count": 0,
                "success_count": 0,
                "failed_count": 0,
            },
        )
        model_row["task_count"] += 1
        if row.get("status") == "succeeded":
            model_row["success_count"] += 1
            model_row["available"] = True
        elif row.get("status") == "failed":
            model_row["failed_count"] += 1

    for provider, entry in (collection_entries or {}).items():
        if not isinstance(entry, dict):
            continue
        provider_row = providers.setdefault(
            str(provider),
            {
                "provider_id": str(provider),
                "task_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "actual_cost_evidence_count": 0,
                "upstream_actual_cost_cny": 0.0,
                "relay_sale_cny": 0.0,
                "last_fetch_at": None,
            },
        )
        provider_row["collection_status"] = str(entry.get("collection_status") or "incomplete")
        provider_row["actual_log_complete"] = entry.get("actual_log_complete") is True
        provider_row["provider_evidence_count"] = len(entry.get("video_task_evidence") or [])
        provider_row["matched_actual_cost_cny"] = round(
            float(provider_row.get("upstream_actual_cost_cny") or 0), 8
        )
        if provider_row["actual_log_complete"] and entry.get("day_log_cost_cny") is not None:
            provider_row["upstream_actual_cost_cny"] = round(
                float(entry.get("day_log_cost_cny") or 0), 8
            )
        else:
            provider_row["upstream_actual_cost_cny"] = None
        provider_row["last_fetch_at"] = entry.get("fetched_at") or provider_row["last_fetch_at"]
        provider_row["collection_error"] = entry.get("collection_error") or entry.get(
            "last_attempt_error"
        )

    private_rows = []
    for provider_row in providers.values():
        provider_row["success_rate"] = _ratio(
            provider_row["success_count"], provider_row["task_count"]
        )
        provider_row["actual_cost_coverage"] = _ratio(
            provider_row["actual_cost_evidence_count"], provider_row["task_count"]
        )
        if provider_row["upstream_actual_cost_cny"] is not None:
            provider_row["upstream_actual_cost_cny"] = round(
                provider_row["upstream_actual_cost_cny"], 8
            )
        provider_row["relay_sale_cny"] = round(provider_row["relay_sale_cny"], 8)
        private_rows.append(provider_row)
    public_rows = []
    for model_row in models.values():
        model_row["success_rate"] = _ratio(model_row["success_count"], model_row["task_count"])
        public_rows.append(model_row)
    return {
        "private": {
            "date": day,
            "generated_at": generated_at,
            "providers": sorted(private_rows, key=lambda row: row["provider_id"]),
            "models": sorted(public_rows, key=lambda row: (row["model"], row["resolution"])),
            "reconciliation": rows,
        },
        "public": {
            "date": day,
            "generated_at": generated_at,
            "models": sorted(public_rows, key=lambda row: (row["model"], row["resolution"])),
        },
    }
