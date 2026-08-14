"""SQLite WAL state store for the XingTu video relay."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"queued", "submitting", "running", "reconciling"}
TERMINAL_STATUSES = {"succeeded", "failed", "uncertain", "pending_review"}
BILLING_CONTRACT_LEGACY = "xtai-video-billing-v2"
BILLING_CONTRACT_VERSION = "xtai-video-billing-v2.1"
BILLING_CONTRACT_REFERENCE_VERSION = "xtai-video-billing-v2.2"
BILLING_CONTRACT_VERSIONS = frozenset({
    BILLING_CONTRACT_LEGACY,
    BILLING_CONTRACT_VERSION,
    BILLING_CONTRACT_REFERENCE_VERSION,
})
PRICE_CONTRACT_VERSION = "xtai-video-pricing-v1"
MONEY_QUANTUM = Decimal("0.000001")
MONEY_LIMIT = Decimal("100000")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
GENERATION_QUARANTINE_FAILURE_THRESHOLD = 2
GENERATION_QUARANTINE_WINDOW_SECONDS = 10 * 60
GENERATION_CAPACITY_FAILURE_THRESHOLD = 2
GENERATION_CAPACITY_WINDOW_SECONDS = 10 * 60
GENERATION_INFRASTRUCTURE_ERROR_CODES = frozenset({
    "provider_credential_refresh_failed",
})
GENERATION_INFRASTRUCTURE_MESSAGE_MARKERS = (
    "refresh leased account credential",
    "invalid adobe refresh response",
    "adobe refresh cookie failed",
)
GENERATION_CAPACITY_ERROR_CODES = frozenset({
    "provider_capacity_exhausted",
})
GENERATION_CAPACITY_MESSAGE_MARKERS = (
    "scheduler claim wait timed out",
)


class StoreConflict(ValueError):
    pass


class Store:
    def __init__(self, data_dir: Path, *, max_active_jobs: int = 500, public_base_url: str = "") -> None:
        self.data_dir = data_dir
        self.max_active_jobs = max(1, min(int(max_active_jobs), 5000))
        self.public_base_url = str(public_base_url or "").rstrip("/")
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "video-jobs.sqlite3"
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma journal_mode=wal")
        connection.execute("pragma synchronous=full")
        connection.execute("pragma foreign_keys=on")
        connection.execute("pragma busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                create table if not exists video_jobs (
                    job_id text primary key,
                    request_id text not null unique,
                    fingerprint text not null,
                    protocol_version text not null,
                    catalog_revision text not null,
                    stable_model text not null,
                    provider_id text not null,
                    upstream_model text not null,
                    adapter_revision text not null,
                    route_plan_json text not null default '[]',
                    route_index integer not null default 0,
                    selection_reason text not null default '',
                    route_history_json text not null default '[]',
                    status text not null,
                    payload_json text not null,
                    result_json text not null default '',
                    error_json text not null default '',
                    upstream_task_id text not null default '',
                    upstream_status text not null default '',
                    submit_attempts integer not null default 0,
                    submit_started_at integer not null default 0,
                    submit_confirmed_at integer not null default 0,
                    poll_attempts integer not null default 0,
                    poll_errors integer not null default 0,
                    missing_count integer not null default 0,
                    missing_last_at integer not null default 0,
                    next_poll_at integer not null default 0,
                    billing_contract_version text not null default '',
                    billing_status text not null default 'unavailable',
                    reserved_cny_exact text not null default '',
                    charged_cny_exact text not null default '',
                    refund_cny_exact text not null default '',
                    supplement_cny_exact text not null default '',
                    official_cost_cny_exact text not null default '',
                    official_pricing_revision text not null default '',
                    billing_markup_exact text not null default '1.5',
                    settlement_revision integer not null default 0,
                    settlement_fingerprint text not null default '',
                    settlement_query_attempts integer not null default 0,
                    settlement_next_query_at integer not null default 0,
                    settlement_query_started_at integer not null default 0,
                    settlement_query_last_error text not null default '',
                    created_at integer not null,
                    updated_at integer not null,
                    finished_at integer not null default 0
                );
                create index if not exists idx_video_jobs_status_poll
                    on video_jobs(status, next_poll_at, updated_at);
                create index if not exists idx_video_jobs_finished
                    on video_jobs(status, finished_at);
                create index if not exists idx_video_jobs_updated
                    on video_jobs(updated_at);
                create table if not exists video_provider_generation_quarantines (
                    provider_id text primary key,
                    status text not null,
                    reason_code text not null,
                    failure_count integer not null,
                    window_seconds integer not null,
                    first_failure_at integer not null,
                    last_failure_at integer not null,
                    activated_at integer not null,
                    cleared_at integer not null default 0
                );
                create index if not exists idx_video_provider_generation_quarantine_status
                    on video_provider_generation_quarantines(status,activated_at);
                create table if not exists video_settlements (
                    settlement_id text primary key,
                    job_id text not null,
                    revision integer not null,
                    provider_task_id text not null,
                    actual_cost_status text not null,
                    actual_cost_cny_exact text not null,
                    previous_charge_cny_exact text not null,
                    charged_cny_exact text not null,
                    delta_cny_exact text not null,
                    evidence_source text not null,
                    evidence_id text not null,
                    observed_at text not null,
                    evidence_fingerprint text not null unique,
                    created_at integer not null,
                    unique(job_id, revision),
                    foreign key(job_id) references video_jobs(job_id)
                );
                create index if not exists idx_video_settlements_job
                    on video_settlements(job_id, revision);
                create table if not exists video_provider_task_bindings (
                    job_id text primary key,
                    provider_id text not null,
                    execution_task_id text not null,
                    billing_task_id text not null,
                    resolver_version text not null,
                    provider_record_id text not null,
                    provider_submit_time integer not null,
                    provider_finish_time integer not null,
                    media_size_bytes integer not null,
                    media_sha256 text not null,
                    evidence_fingerprint text not null,
                    created_at integer not null,
                    unique(provider_id,execution_task_id),
                    unique(provider_id,billing_task_id),
                    foreign key(job_id) references video_jobs(job_id) on delete cascade
                );
                create unique index if not exists idx_video_provider_binding_evidence
                    on video_provider_task_bindings(evidence_fingerprint);
                create table if not exists video_job_attempts (
                    attempt_id text primary key,
                    job_id text not null,
                    route_index integer not null,
                    provider_id text not null,
                    upstream_model text not null,
                    idempotency_key text not null,
                    state text not null,
                    execution_task_id text not null default '',
                    billing_task_id text not null default '',
                    actual_cost_cny_exact text not null default '',
                    evidence_source text not null default '',
                    evidence_id text not null default '',
                    observed_at text not null default '',
                    submit_count integer not null default 0,
                    created_at integer not null,
                    updated_at integer not null,
                    unique(job_id,route_index),
                    unique(provider_id,idempotency_key),
                    foreign key(job_id) references video_jobs(job_id) on delete cascade
                );
                create unique index if not exists idx_video_attempt_evidence
                    on video_job_attempts(evidence_id) where evidence_id<>'';
                create index if not exists idx_video_attempt_job
                    on video_job_attempts(job_id,route_index);
                create table if not exists video_webhook_outbox (
                    event_id text primary key,
                    job_id text not null,
                    event_type text not null,
                    event_revision integer not null,
                    payload_json text not null,
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    next_attempt_at integer not null default 0,
                    last_error text not null default '',
                    created_at integer not null,
                    updated_at integer not null,
                    delivered_at integer not null default 0,
                    unique(job_id, event_type, event_revision),
                    foreign key(job_id) references video_jobs(job_id)
                );
                create index if not exists idx_video_webhook_due
                    on video_webhook_outbox(status, next_attempt_at, created_at);
                """
            )
            existing_columns = {
                str(row[1]) for row in connection.execute("pragma table_info(video_jobs)").fetchall()
            }
            migrations = {
                "route_plan_json": "text not null default '[]'",
                "route_index": "integer not null default 0",
                "selection_reason": "text not null default ''",
                "route_history_json": "text not null default '[]'",
                "billing_contract_version": "text not null default ''",
                "billing_status": "text not null default 'unavailable'",
                "reserved_cny_exact": "text not null default ''",
                "charged_cny_exact": "text not null default ''",
                "refund_cny_exact": "text not null default ''",
                "supplement_cny_exact": "text not null default ''",
                "official_cost_cny_exact": "text not null default ''",
                "official_pricing_revision": "text not null default ''",
                "billing_markup_exact": "text not null default '1.5'",
                "settlement_revision": "integer not null default 0",
                "settlement_fingerprint": "text not null default ''",
                "settlement_query_attempts": "integer not null default 0",
                "settlement_next_query_at": "integer not null default 0",
                "settlement_query_started_at": "integer not null default 0",
                "settlement_query_last_error": "text not null default ''",
                "submit_started_at": "integer not null default 0",
                "submit_confirmed_at": "integer not null default 0",
                "recovery_started_at": "integer not null default 0",
                "recovery_deadline_at": "integer not null default 0",
                "recovery_next_at": "integer not null default 0",
                "recovery_attempts": "integer not null default 0",
                "recovery_last_error": "text not null default ''",
            }
            for name, definition in migrations.items():
                if name not in existing_columns:
                    connection.execute(f"alter table video_jobs add column {name} {definition}")
            attempt_columns = {
                str(row[1]) for row in connection.execute("pragma table_info(video_job_attempts)").fetchall()
            }
            if "billing_task_id" not in attempt_columns:
                connection.execute(
                    "alter table video_job_attempts add column billing_task_id text not null default ''"
                )
            connection.execute(
                """
                create index if not exists idx_video_jobs_settlement_query
                on video_jobs(billing_status,provider_id,settlement_next_query_at,settlement_query_started_at)
                """
            )
            connection.execute(
                """
                update video_jobs
                set billing_contract_version=''
                where billing_status='unavailable'
                  and reserved_cny_exact=''
                  and official_cost_cny_exact=''
                """
            )

    @staticmethod
    def snapshot(row: sqlite3.Row | dict[str, Any], *, include_result: bool = True) -> dict[str, Any]:
        source = dict(row)
        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        billing_status = str(source.get("billing_status") or "unavailable")
        result_ready = billing_status in {"settled", "unavailable"}
        if (
            include_result
            and source.get("status") == "succeeded"
            and result_ready
            and source.get("result_json")
        ):
            result = _json_object(str(source.get("result_json") or ""))
        if source.get("error_json"):
            error = _json_object(str(source.get("error_json") or ""))
        billing = {
            "contract_version": str(source.get("billing_contract_version") or ""),
            "status": billing_status,
            "currency": "CNY",
            "reserve_basis": "ark_official_1_5" if source.get("reserved_cny_exact") else "",
            "reserved_amount": _public_money(source.get("reserved_cny_exact")),
            "charged_amount": _public_money(source.get("charged_cny_exact")),
            "refund_amount": _public_money(source.get("refund_cny_exact")),
            "supplement_amount": _public_money(source.get("supplement_cny_exact")),
            "settlement_revision": int(source.get("settlement_revision") or 0),
            "pricing_revision": str(source.get("official_pricing_revision") or ""),
            "settled_at": (
                datetime.fromtimestamp(int(source.get("updated_at") or 0), timezone(timedelta(hours=8))).isoformat(timespec="seconds")
                if billing_status == "settled" and int(source.get("updated_at") or 0) > 0
                else None
            ),
        }
        status = str(source.get("status") or "")
        if status == "succeeded":
            delivery = "ready" if result_ready else "pending_settlement"
        else:
            delivery = "unavailable"
        public_status = "running" if status == "reconciling" else status
        snapshot = {
            "job_id": source.get("job_id"),
            "request_id": source.get("request_id"),
            "protocol_version": source.get("protocol_version"),
            "catalog_revision": source.get("catalog_revision"),
            "model": source.get("stable_model"),
            "status": public_status,
            "created_at": int(source.get("created_at") or 0),
            "updated_at": int(source.get("updated_at") or 0),
            "finished_at": int(source.get("finished_at") or 0),
            "error": error,
            "result": result,
            "result_delivery": delivery,
            "billing": billing,
            "recovery": {
                "phase": "reconciling" if status == "reconciling" else "",
                "attempt": int(source.get("route_index") or 0) + 1,
            },
            "result_expired": bool(
                source.get("status") == "succeeded"
                and result_ready
                and source.get("finished_at")
                and not source.get("result_json")
            ),
        }
        if str(source.get("billing_contract_version") or "") == BILLING_CONTRACT_REFERENCE_VERSION:
            payload = _json_object(str(source.get("payload_json") or ""))
            reference_input = payload.get("reference_input") if isinstance(payload, dict) else None
            if isinstance(reference_input, dict):
                videos = reference_input.get("reference_videos") if isinstance(reference_input.get("reference_videos"), list) else []
                audios = reference_input.get("reference_audios") if isinstance(reference_input.get("reference_audios"), list) else []
                snapshot["input"] = {
                    "reference_video_count": len(videos),
                    "reference_video_digest": _reference_items_digest(videos),
                    "reference_video_total_duration_seconds": _reference_duration_total(videos),
                    "reference_audio_count": len(audios),
                    "reference_audio_digest": _reference_items_digest(audios),
                    "reference_audio_total_duration_seconds": _reference_duration_total(audios),
                }
        return snapshot

    def create(
        self,
        *,
        request_id: str,
        fingerprint: str,
        protocol_version: str,
        catalog_revision: str,
        stable_model: str,
        provider_id: str,
        upstream_model: str,
        adapter_revision: str,
        payload_json: str,
        route_plan: list[dict[str, Any]] | None = None,
        selection_reason: str = "",
    ) -> tuple[dict[str, Any], bool]:
        current = int(time.time())
        reservation = _reservation_from_payload(payload_json)
        plan = _route_plan(
            route_plan,
            provider_id=provider_id,
            upstream_model=upstream_model,
            adapter_revision=adapter_revision,
        )
        route_plan_json = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute("begin immediate")
            existing = connection.execute("select * from video_jobs where request_id=?", (request_id,)).fetchone()
            if existing:
                connection.commit()
                if str(existing["fingerprint"]) != fingerprint:
                    raise StoreConflict("request_id already belongs to another payload")
                return self.snapshot(existing), True
            active = connection.execute(
                "select count(*) from video_jobs where status in ('queued','submitting','running','reconciling')"
            ).fetchone()[0]
            if int(active or 0) >= self.max_active_jobs:
                connection.rollback()
                raise StoreConflict("video job gateway queue is full")
            job_id = f"vjob_{uuid.uuid4().hex}"
            connection.execute(
                """
                insert into video_jobs(
                    job_id,request_id,fingerprint,protocol_version,catalog_revision,
                    stable_model,provider_id,upstream_model,adapter_revision,
                    route_plan_json,route_index,selection_reason,route_history_json,status,
                    payload_json,billing_contract_version,billing_status,reserved_cny_exact,official_cost_cny_exact,
                    official_pricing_revision,billing_markup_exact,created_at,updated_at
                ) values(?,?,?,?,?,?,?,?,?,?,0,?,'[]','queued',?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    request_id,
                    fingerprint,
                    protocol_version,
                    catalog_revision,
                    stable_model,
                    provider_id,
                    upstream_model,
                    adapter_revision,
                    route_plan_json,
                    str(selection_reason or "")[:120],
                    payload_json,
                    reservation["contract_version"] if reservation["status"] == "reserved" else "",
                    reservation["status"],
                    reservation["reserved"],
                    reservation["official"],
                    reservation["revision"],
                    "1.5",
                    current,
                    current,
                ),
            )
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            connection.commit()
            return self.snapshot(row), False

    def get(self, *, job_id: str = "", request_id: str = "", internal: bool = False) -> dict[str, Any] | None:
        with self.connect() as connection:
            if job_id:
                row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            else:
                row = connection.execute("select * from video_jobs where request_id=?", (request_id,)).fetchone()
        if not row:
            return None
        return dict(row) if internal else self.snapshot(row)

    def claim_submit(self, job_id: str) -> dict[str, Any] | None:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            if not row or row["status"] != "queued":
                connection.commit()
                return None
            idempotency_key = _attempt_idempotency_key(row)
            attempt_id = "vat_" + hashlib.sha256(
                f"{job_id}\0{int(row['route_index'] or 0)}".encode("utf-8")
            ).hexdigest()[:32]
            connection.execute(
                """
                insert into video_job_attempts(
                    attempt_id,job_id,route_index,provider_id,upstream_model,
                    idempotency_key,state,submit_count,created_at,updated_at
                ) values(?,?,?,?,?,?,'submitting',1,?,?)
                on conflict(job_id,route_index) do update set
                    state='submitting',submit_count=submit_count+1,updated_at=excluded.updated_at
                """,
                (
                    attempt_id,
                    job_id,
                    int(row["route_index"] or 0),
                    str(row["provider_id"] or ""),
                    str(row["upstream_model"] or ""),
                    idempotency_key,
                    current,
                    current,
                ),
            )
            connection.execute(
                """
                update video_jobs
                set status='submitting',submit_attempts=submit_attempts+1,
                    submit_started_at=?,submit_confirmed_at=0,updated_at=?
                where job_id=? and status='queued'
                """,
                (current, current, job_id),
            )
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            connection.commit()
            result = dict(row)
            result["_submission_request_id"] = idempotency_key
            return result

    def advance_route(self, job_id: str, *, error: dict[str, Any]) -> bool:
        """Move a definitively rejected pre-creation submit to its persisted fallback."""
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            if not row or row["status"] != "submitting" or str(row["upstream_task_id"] or ""):
                connection.commit()
                return False
            try:
                plan = json.loads(str(row["route_plan_json"] or "[]"))
                history = json.loads(str(row["route_history_json"] or "[]"))
            except json.JSONDecodeError:
                connection.commit()
                return False
            index = int(row["route_index"] or 0)
            next_index = index + 1
            if not isinstance(plan, list) or next_index >= len(plan) or not isinstance(plan[next_index], dict):
                connection.commit()
                return False
            next_route = plan[next_index]
            provider_id = str(next_route.get("provider_id") or "").strip().lower()
            upstream_model = str(next_route.get("upstream_model") or "").strip()
            adapter_revision = str(next_route.get("adapter_revision") or "").strip()
            if not provider_id or not upstream_model or not adapter_revision:
                connection.commit()
                return False
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "route_index": index,
                    "provider_id": str(row["provider_id"] or ""),
                    "error": {
                        "code": str(error.get("code") or "upstream_rejected")[:80],
                        "uncertain": bool(error.get("uncertain", False)),
                    },
                    "at": current,
                }
            )
            connection.execute(
                """
                update video_jobs
                set provider_id=?,upstream_model=?,adapter_revision=?,route_index=?,
                    route_history_json=?,status='queued',error_json='',updated_at=?
                where job_id=? and status='submitting' and upstream_task_id=''
                """,
                (
                    provider_id,
                    upstream_model,
                    adapter_revision,
                    next_index,
                    json.dumps(history, ensure_ascii=False, separators=(",", ":")),
                    current,
                    job_id,
                ),
            )
            changed = connection.total_changes > 0
            connection.commit()
            return changed

    def mark_running(self, job_id: str, upstream_task_id: str, upstream_status: str, poll_delay: int) -> None:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            connection.execute(
                """
                update video_jobs
                set status='running',upstream_task_id=?,upstream_status=?,error_json='',
                    submit_confirmed_at=case when submit_confirmed_at=0 then ? else submit_confirmed_at end,
                    next_poll_at=?,updated_at=?
                where job_id=? and status='submitting'
                """,
                (
                    upstream_task_id[:200],
                    upstream_status[:80],
                    current,
                    current + max(1, poll_delay),
                    current,
                    job_id,
                ),
            )
            connection.execute(
                """
                update video_job_attempts
                set state='running',execution_task_id=?,updated_at=?
                where job_id=? and route_index=(select route_index from video_jobs where job_id=?)
                """,
                (upstream_task_id[:200], current, job_id, job_id),
            )
            connection.commit()

    def begin_recovery(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
        upstream_task_id: str = "",
        upstream_status: str = "",
        delay_seconds: int = 15,
        deadline_seconds: int = 8 * 3600,
    ) -> bool:
        """Persist an uncertain or failed attempt for unattended reconciliation."""
        current = int(time.time())
        error_json = json.dumps(error or {}, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            if not row or str(row["status"] or "") not in {"submitting", "running", "reconciling"}:
                connection.commit()
                return False
            task_id = str(upstream_task_id or row["upstream_task_id"] or "")[:200]
            connection.execute(
                """
                update video_jobs
                set status='reconciling',error_json=?,upstream_task_id=?,upstream_status=?,
                    billing_status=case when reserved_cny_exact<>'' then 'recovery_pending' else 'unavailable' end,
                    recovery_started_at=case when recovery_started_at=0 then ? else recovery_started_at end,
                    recovery_deadline_at=case when recovery_deadline_at=0 then ? else recovery_deadline_at end,
                    recovery_next_at=?,recovery_last_error='',next_poll_at=0,updated_at=?,finished_at=0
                where job_id=?
                """,
                (
                    error_json,
                    task_id,
                    str(upstream_status or row["upstream_status"] or "")[:80],
                    current,
                    min(
                        current + max(300, min(int(deadline_seconds), 48 * 3600)),
                        int(row["created_at"] or current) + max(300, min(int(deadline_seconds), 48 * 3600)),
                    ),
                    current + max(1, min(int(delay_seconds), 3600)),
                    current,
                    job_id,
                ),
            )
            connection.execute(
                """
                update video_job_attempts set state='reconciling',execution_task_id=?,updated_at=?
                where job_id=? and route_index=?
                """,
                (task_id, current, job_id, int(row["route_index"] or 0)),
            )
            connection.commit()
            return True

    def due_recovery_jobs(self, *, limit: int = 20, lease_seconds: int = 60) -> list[dict[str, Any]]:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            rows = connection.execute(
                """
                select * from video_jobs
                where status='reconciling' and recovery_next_at<=?
                order by recovery_next_at,updated_at limit ?
                """,
                (current, max(1, min(int(limit), 100))),
            ).fetchall()
            if rows:
                connection.executemany(
                    """
                    update video_jobs set recovery_next_at=?,recovery_attempts=recovery_attempts+1
                    where job_id=? and status='reconciling'
                    """,
                    [(current + max(15, int(lease_seconds)), str(row["job_id"])) for row in rows],
                )
            connection.commit()
        return [dict(row) for row in rows]

    def retry_recovery(self, job_id: str, *, delay_seconds: int, error_code: str) -> None:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """
                update video_jobs set recovery_next_at=?,recovery_last_error=?,updated_at=?
                where job_id=? and status='reconciling'
                """,
                (current + max(15, min(int(delay_seconds), 3600)), str(error_code or "recovery_pending")[:120], current, job_id),
            )

    def retry_uncertain_submit(self, job_id: str, *, max_same_route_submits: int = 2) -> bool:
        """Retry the same persisted route and idempotency key; never cross-provider blindly."""
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            attempt = connection.execute(
                "select * from video_job_attempts where job_id=? and route_index=?",
                (job_id, int(row["route_index"] or 0) if row else -1),
            ).fetchone()
            if (
                not row
                or str(row["status"] or "") != "reconciling"
                or str(row["upstream_task_id"] or "")
                or not attempt
                or int(attempt["submit_count"] or 0) >= max(1, int(max_same_route_submits))
            ):
                connection.commit()
                return False
            connection.execute(
                """
                update video_jobs set status='queued',billing_status=case when reserved_cny_exact<>'' then 'reserved' else 'unavailable' end,
                    error_json='',recovery_next_at=0,recovery_last_error='',updated_at=? where job_id=?
                """,
                (current, job_id),
            )
            connection.execute(
                "update video_job_attempts set state='prepared',updated_at=? where attempt_id=?",
                (current, str(attempt["attempt_id"])),
            )
            connection.commit()
            return True

    def complete_failed_attempt(
        self,
        job_id: str,
        *,
        provider_task_id: str,
        actual_cost_cny_exact: str,
        evidence_source: str,
        evidence_id: str,
        observed_at: str,
        error: dict[str, Any],
    ) -> bool:
        """Record authoritative failed-attempt cost and atomically advance one route."""
        amount = _money_exact(actual_cost_cny_exact, allow_zero=True)
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            if not row or str(row["status"] or "") != "reconciling":
                connection.commit()
                return False
            index = int(row["route_index"] or 0)
            attempt = connection.execute(
                "select * from video_job_attempts where job_id=? and route_index=?",
                (job_id, index),
            ).fetchone()
            if not attempt:
                connection.rollback()
                raise StoreConflict("video recovery attempt is missing")
            existing_evidence = str(attempt["evidence_id"] or "")
            if existing_evidence and existing_evidence != str(evidence_id or ""):
                connection.rollback()
                raise StoreConflict("video recovery attempt has conflicting evidence")
            connection.execute(
                """
                update video_job_attempts
                set state='failed',execution_task_id=?,billing_task_id=?,actual_cost_cny_exact=?,evidence_source=?,
                    evidence_id=?,observed_at=?,updated_at=? where attempt_id=?
                """,
                (
                    str(row["upstream_task_id"] or "")[:200], str(provider_task_id or "")[:200], amount,
                    str(evidence_source or "")[:120], str(evidence_id or "")[:240],
                    str(observed_at or "")[:80], current, str(attempt["attempt_id"]),
                ),
            )
            try:
                plan = json.loads(str(row["route_plan_json"] or "[]"))
                history = json.loads(str(row["route_history_json"] or "[]"))
            except json.JSONDecodeError as exc:
                connection.rollback()
                raise StoreConflict("video recovery route plan is invalid") from exc
            next_index = index + 1
            aggregate_cost = _sum_attempt_costs(connection, job_id)
            try:
                cost_guard = Decimal(_money_exact(row["reserved_cny_exact"], allow_zero=False))
            except StoreConflict:
                cost_guard = Decimal("0")
            within_cost_guard = cost_guard <= 0 or aggregate_cost <= cost_guard
            if (
                within_cost_guard
                and isinstance(plan, list)
                and next_index < min(len(plan), 4)
                and isinstance(plan[next_index], dict)
            ):
                next_route = plan[next_index]
                provider_id = str(next_route.get("provider_id") or "").strip().lower()
                upstream_model = str(next_route.get("upstream_model") or "").strip()
                adapter_revision = str(next_route.get("adapter_revision") or "").strip()
                if not provider_id or not upstream_model or not adapter_revision:
                    connection.rollback()
                    raise StoreConflict("video recovery fallback route is invalid")
                if not isinstance(history, list):
                    history = []
                history.append({
                    "route_index": index,
                    "provider_id": str(row["provider_id"] or ""),
                    "error": {"code": str(error.get("code") or "upstream_failed")[:80], "uncertain": False},
                    "billing": {"status": "verified", "actual_cost_cny_exact": amount},
                    "at": current,
                })
                connection.execute(
                    """
                    update video_jobs set provider_id=?,upstream_model=?,adapter_revision=?,route_index=?,
                        route_history_json=?,status='queued',upstream_task_id='',upstream_status='',
                        error_json='',billing_status=case when reserved_cny_exact<>'' then 'reserved' else 'unavailable' end,
                        recovery_started_at=0,recovery_deadline_at=0,recovery_next_at=0,
                        recovery_attempts=0,recovery_last_error='',submit_confirmed_at=0,
                        missing_count=0,missing_last_at=0,poll_errors=0,updated_at=? where job_id=?
                    """,
                    (
                        provider_id, upstream_model, adapter_revision, next_index,
                        json.dumps(history, ensure_ascii=False, separators=(",", ":")), current, job_id,
                    ),
                )
                connection.commit()
                return True
            aggregate_cost_exact = _quantize_money(aggregate_cost)
            aggregate_evidence_id = "aggregate-failed:" + hashlib.sha256(
                f"{job_id}\0{aggregate_cost_exact}\0{evidence_id}".encode("utf-8")
            ).hexdigest()
            connection.commit()
        self.finish(
            job_id,
            "failed",
            error=error,
            upstream_task_id=str(row["upstream_task_id"] or provider_task_id),
            upstream_status="failed",
            defer_failed_settlement=bool(str(row["reserved_cny_exact"] or "")),
        )
        if str(row["reserved_cny_exact"] or ""):
            terminal = self.get(job_id=job_id, internal=True) or {}
            evidence = build_settlement_evidence(
                job_id=job_id,
                revision=int(terminal.get("settlement_revision") or 0) + 1,
                provider_task_id=provider_task_id,
                actual_cost_status="zero_verified" if aggregate_cost == 0 else "actual",
                actual_cost_cny_exact=aggregate_cost_exact,
                evidence_source="xtai_aggregate_attempt_cost",
                evidence_id=aggregate_evidence_id,
                observed_at=observed_at,
                contract_version=str(terminal.get("billing_contract_version") or ""),
            )
            self.apply_settlement(evidence)
        return False

    def attempt_cost_total(self, job_id: str, *, include_current: bool = True) -> str:
        with self.connect() as connection:
            total = _sum_attempt_costs(connection, job_id)
        return _quantize_money(total)

    def record_success_attempt_cost(self, job_id: str, record: Any) -> str:
        amount = _money_exact(getattr(record, "actual_cost_cny_exact", ""), allow_zero=True)
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            if not row:
                connection.rollback()
                raise StoreConflict("video job does not exist")
            attempt = connection.execute(
                "select * from video_job_attempts where job_id=? and route_index=?",
                (job_id, int(row["route_index"] or 0)),
            ).fetchone()
            if not attempt:
                attempt_id = "vat_" + hashlib.sha256(
                    f"{job_id}\0{int(row['route_index'] or 0)}".encode("utf-8")
                ).hexdigest()[:32]
                connection.execute(
                    """
                    insert into video_job_attempts(
                        attempt_id,job_id,route_index,provider_id,upstream_model,
                        idempotency_key,state,execution_task_id,submit_count,created_at,updated_at
                    ) values(?,?,?,?,?,?,'succeeded',?,1,?,?)
                    """,
                    (
                        attempt_id, job_id, int(row["route_index"] or 0),
                        str(row["provider_id"] or ""), str(row["upstream_model"] or ""),
                        _attempt_idempotency_key(row), str(row["upstream_task_id"] or ""),
                        current, current,
                    ),
                )
                attempt = connection.execute(
                    "select * from video_job_attempts where attempt_id=?", (attempt_id,)
                ).fetchone()
            existing = str(attempt["evidence_id"] or "")
            if existing and existing != str(getattr(record, "evidence_id", "")):
                connection.rollback()
                raise StoreConflict("video success attempt has conflicting evidence")
            connection.execute(
                """
                update video_job_attempts set state='succeeded',actual_cost_cny_exact=?,
                    evidence_source=?,evidence_id=?,observed_at=?,updated_at=? where attempt_id=?
                """,
                (
                    amount, str(getattr(record, "evidence_source", ""))[:120],
                    str(getattr(record, "evidence_id", ""))[:240],
                    str(getattr(record, "observed_at", ""))[:80], current, str(attempt["attempt_id"]),
                ),
            )
            total = _sum_attempt_costs(connection, job_id)
            connection.commit()
        return _quantize_money(total)

    def get_provider_task_binding(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "select * from video_provider_task_bindings where job_id=?",
                (str(job_id or "").strip(),),
            ).fetchone()
        return dict(row) if row else None

    def bind_provider_task(
        self,
        *,
        job_id: str,
        provider_id: str,
        execution_task_id: str,
        billing_task_id: str,
        resolver_version: str,
        provider_record_id: str,
        provider_submit_time: int,
        provider_finish_time: int,
        media_size_bytes: int,
        media_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        binding = _validated_provider_binding(
            {
                "job_id": job_id,
                "provider_id": provider_id,
                "execution_task_id": execution_task_id,
                "billing_task_id": billing_task_id,
                "resolver_version": resolver_version,
                "provider_record_id": provider_record_id,
                "provider_submit_time": provider_submit_time,
                "provider_finish_time": provider_finish_time,
                "media_size_bytes": media_size_bytes,
                "media_sha256": media_sha256,
            }
        )
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from video_jobs where job_id=?", (binding["job_id"],)
            ).fetchone()
            if not row:
                connection.rollback()
                raise StoreConflict("provider task binding job does not exist")
            if str(row["provider_id"] or "").strip().lower() != binding["provider_id"]:
                connection.rollback()
                raise StoreConflict("provider task binding provider does not match the video job")
            if str(row["upstream_task_id"] or "") != binding["execution_task_id"]:
                connection.rollback()
                raise StoreConflict("provider execution task identity does not match the video job")
            existing = connection.execute(
                "select * from video_provider_task_bindings where job_id=?",
                (binding["job_id"],),
            ).fetchone()
            if existing:
                if str(existing["evidence_fingerprint"] or "") != binding["evidence_fingerprint"]:
                    connection.rollback()
                    raise StoreConflict("video job already has a different provider task binding")
                connection.commit()
                return dict(existing), True
            if str(row["status"] or "") != "succeeded" or str(
                row["billing_status"] or ""
            ) != "settlement_pending":
                connection.rollback()
                raise StoreConflict("only a succeeded unsettled video job can bind billing identity")
            try:
                connection.execute(
                    """
                    insert into video_provider_task_bindings(
                        job_id,provider_id,execution_task_id,billing_task_id,resolver_version,
                        provider_record_id,provider_submit_time,provider_finish_time,
                        media_size_bytes,media_sha256,evidence_fingerprint,created_at
                    ) values(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        binding["job_id"],
                        binding["provider_id"],
                        binding["execution_task_id"],
                        binding["billing_task_id"],
                        binding["resolver_version"],
                        binding["provider_record_id"],
                        binding["provider_submit_time"],
                        binding["provider_finish_time"],
                        binding["media_size_bytes"],
                        binding["media_sha256"],
                        binding["evidence_fingerprint"],
                        current,
                    ),
                )
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise StoreConflict("provider task identity is already bound") from error
            inserted = connection.execute(
                "select * from video_provider_task_bindings where job_id=?",
                (binding["job_id"],),
            ).fetchone()
            connection.commit()
            return dict(inserted), False

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        upstream_task_id: str = "",
        upstream_status: str = "",
        defer_failed_settlement: bool = False,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal video job status")
        current = int(time.time())
        result_json = json.dumps(result or {}, ensure_ascii=False, separators=(",", ":")) if result else ""
        error_json = json.dumps(error or {}, ensure_ascii=False, separators=(",", ":")) if error else ""
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select status,upstream_task_id,provider_id from video_jobs where job_id=?",
                (job_id,),
            ).fetchone()
            if row and row["status"] in ACTIVE_STATUSES:
                task_id = upstream_task_id or str(row["upstream_task_id"] or "")
                billing_row = connection.execute(
                    "select reserved_cny_exact,billing_status from video_jobs where job_id=?",
                    (job_id,),
                ).fetchone()
                reserved = str(billing_row["reserved_cny_exact"] or "") if billing_row else ""
                if status == "succeeded":
                    billing_status = "settlement_pending" if reserved else "unavailable"
                    charged = ""
                    refund = ""
                elif status == "failed" and reserved:
                    if defer_failed_settlement:
                        billing_status = "settlement_pending"
                        charged = ""
                        refund = ""
                    else:
                        billing_status = "refunded"
                        charged = "0.000000"
                        refund = _money_exact(reserved, allow_zero=False)
                elif status in {"uncertain", "pending_review"} and reserved:
                    billing_status = "pending_review"
                    charged = ""
                    refund = ""
                else:
                    billing_status = "unavailable"
                    charged = ""
                    refund = ""
                connection.execute(
                    """
                    update video_jobs
                    set status=?,result_json=?,error_json=?,upstream_task_id=?,upstream_status=?,
                        submit_confirmed_at=case
                            when ?<>'' and submit_confirmed_at=0 then ?
                            else submit_confirmed_at end,
                        billing_status=?,charged_cny_exact=?,refund_cny_exact=?,
                        supplement_cny_exact='',
                        settlement_next_query_at=?,settlement_query_started_at=0,
                        settlement_query_last_error='',
                        next_poll_at=0,updated_at=?,finished_at=?
                    where job_id=?
                    """,
                    (
                        status,
                        result_json,
                        error_json,
                        task_id[:200],
                        upstream_status[:80],
                        task_id[:200],
                        current,
                        billing_status,
                        charged,
                        refund,
                        current if billing_status == "settlement_pending" else 0,
                        current,
                        current,
                        job_id,
                    ),
                )
                updated = connection.execute(
                    "select * from video_jobs where job_id=?", (job_id,)
                ).fetchone()
                if updated and status == "failed":
                    self._record_generation_infrastructure_failure(
                        connection,
                        job_id=job_id,
                        provider_id=str(row["provider_id"] or ""),
                        error=error or {},
                        current=current,
                    )
                if updated and reserved:
                    event_type = {
                        "succeeded": "video.task.succeeded",
                        "failed": "video.task.failed",
                        "uncertain": "video.billing.pending_review",
                        "pending_review": "video.billing.pending_review",
                    }[status]
                    self._enqueue_webhook(connection, updated, event_type, 1)
            connection.commit()

    def apply_settlement(self, payload: Any) -> tuple[dict[str, Any], bool]:
        evidence = _validated_settlement(payload)
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            existing = connection.execute(
                "select * from video_settlements where settlement_id=?",
                (evidence["settlement_id"],),
            ).fetchone()
            if existing:
                if (
                    str(existing["job_id"]) != evidence["job_id"]
                    or int(existing["revision"]) != evidence["revision"]
                    or str(existing["evidence_fingerprint"])
                    != evidence["evidence_fingerprint"]
                ):
                    connection.rollback()
                    raise StoreConflict("settlement_id already belongs to different evidence")
                row = connection.execute(
                    "select * from video_jobs where job_id=?", (evidence["job_id"],)
                ).fetchone()
                connection.commit()
                return self.snapshot(row), True

            row = connection.execute(
                "select * from video_jobs where job_id=?", (evidence["job_id"],)
            ).fetchone()
            if not row:
                connection.rollback()
                raise StoreConflict("video settlement job does not exist")
            if str(row["status"]) not in {"succeeded", "failed"}:
                connection.rollback()
                raise StoreConflict("only a terminal video job can be settled")
            if str(row["billing_status"]) not in {"settlement_pending", "refunded", "settled"}:
                connection.rollback()
                raise StoreConflict("video job has no settleable reservation")
            if str(row["billing_contract_version"] or "") != evidence["contract_version"]:
                connection.rollback()
                raise StoreConflict("settlement contract does not match the video job")
            binding = connection.execute(
                "select * from video_provider_task_bindings where job_id=?",
                (evidence["job_id"],),
            ).fetchone()
            if binding and (
                str(binding["provider_id"] or "") != str(row["provider_id"] or "")
                or str(binding["execution_task_id"] or "") != str(row["upstream_task_id"] or "")
                or str(binding["billing_task_id"] or "") != evidence["provider_task_id"]
            ):
                connection.rollback()
                raise StoreConflict("provider task binding does not match the settlement evidence")
            if not binding and str(row["upstream_task_id"]) != evidence["provider_task_id"]:
                attempt = connection.execute(
                    """
                    select 1 from video_job_attempts
                    where job_id=? and billing_task_id=? and evidence_id<>''
                    """,
                    (evidence["job_id"], evidence["provider_task_id"]),
                ).fetchone()
                if not attempt:
                    connection.rollback()
                    raise StoreConflict("provider task identity does not match the video job")
            expected_revision = int(row["settlement_revision"] or 0) + 1
            if evidence["revision"] != expected_revision:
                connection.rollback()
                raise StoreConflict("settlement revision is not the next revision")

            reserved = Decimal(_money_exact(row["reserved_cny_exact"], allow_zero=False))
            actual = Decimal(evidence["actual_cost_cny_exact"])
            charged_value = Decimal(_quantize_money(actual * Decimal("1.5")))
            charged = _quantize_money(charged_value)
            refund = _quantize_money(max(reserved - charged_value, Decimal("0")))
            supplement = _quantize_money(max(charged_value - reserved, Decimal("0")))
            previous = (
                Decimal(str(row["charged_cny_exact"]))
                if str(row["charged_cny_exact"] or "")
                else reserved
            )
            delta = _quantize_signed_money(charged_value - previous)
            try:
                connection.execute(
                    """
                    insert into video_settlements(
                        settlement_id,job_id,revision,provider_task_id,actual_cost_status,
                        actual_cost_cny_exact,previous_charge_cny_exact,charged_cny_exact,
                        delta_cny_exact,evidence_source,evidence_id,observed_at,
                        evidence_fingerprint,created_at
                    ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        evidence["settlement_id"],
                        evidence["job_id"],
                        evidence["revision"],
                        evidence["provider_task_id"],
                        evidence["actual_cost_status"],
                        evidence["actual_cost_cny_exact"],
                        _quantize_money(previous),
                        charged,
                        delta,
                        evidence["evidence_source"],
                        evidence["evidence_id"],
                        evidence["observed_at"],
                        evidence["evidence_fingerprint"],
                        current,
                    ),
                )
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise StoreConflict("settlement evidence or revision was already used") from error
            connection.execute(
                """
                update video_jobs
                set billing_status='settled',charged_cny_exact=?,refund_cny_exact=?,
                    supplement_cny_exact=?,settlement_revision=?,settlement_fingerprint=?,
                    settlement_next_query_at=0,settlement_query_started_at=0,
                    settlement_query_last_error='',
                    updated_at=?
                where job_id=? and status in ('succeeded','failed')
                """,
                (
                    charged,
                    refund,
                    supplement,
                    evidence["revision"],
                    evidence["evidence_fingerprint"],
                    current,
                    evidence["job_id"],
                ),
            )
            updated = connection.execute(
                "select * from video_jobs where job_id=?", (evidence["job_id"],)
            ).fetchone()
            self._enqueue_webhook(
                connection,
                updated,
                "video.billing.settled",
                int(evidence["revision"]),
            )
            connection.commit()
            return self.snapshot(updated), False

    def due_settlement_jobs(
        self,
        provider_ids: set[str],
        *,
        limit: int = 4,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        providers = sorted({str(value or "").strip().lower() for value in provider_ids if value})
        if not providers:
            return []
        current = int(time.time())
        lease = max(15, min(int(lease_seconds), 300))
        placeholders = ",".join("?" for _ in providers)
        with self.connect() as connection:
            connection.execute("begin immediate")
            rows = connection.execute(
                f"""
                select * from video_jobs
                where status='succeeded'
                  and billing_status='settlement_pending'
                  and upstream_task_id<>''
                  and provider_id in ({placeholders})
                  and settlement_next_query_at<=?
                  and (settlement_query_started_at=0 or settlement_query_started_at<=?)
                order by settlement_next_query_at,finished_at
                limit ?
                """,
                (*providers, current, current - lease, max(1, min(int(limit), 50))),
            ).fetchall()
            if rows:
                connection.executemany(
                    """
                    update video_jobs
                    set settlement_query_attempts=settlement_query_attempts+1,
                        settlement_next_query_at=?,settlement_query_started_at=?
                    where job_id=? and billing_status='settlement_pending'
                    """,
                    [(current + lease, current, str(row["job_id"])) for row in rows],
                )
                job_ids = [str(row["job_id"]) for row in rows]
                selected = ",".join("?" for _ in job_ids)
                rows = connection.execute(
                    f"select * from video_jobs where job_id in ({selected}) order by finished_at",
                    job_ids,
                ).fetchall()
            connection.commit()
        return [dict(row) for row in rows]

    def retry_settlement_collection(self, job_id: str, *, delay_seconds: int, error_code: str) -> None:
        current = int(time.time())
        safe_code = str(error_code or "provider_billing_query_failed")[:120]
        with self.connect() as connection:
            connection.execute(
                """
                update video_jobs
                set settlement_next_query_at=?,settlement_query_started_at=0,
                    settlement_query_last_error=?,updated_at=?
                where job_id=? and status='succeeded' and billing_status='settlement_pending'
                """,
                (
                    current + max(15, min(int(delay_seconds), 3600)),
                    safe_code,
                    current,
                    job_id,
                ),
            )

    def _enqueue_webhook(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        event_type: str,
        revision: int,
    ) -> None:
        job_id = str(row["job_id"])
        contract_version = str(row["billing_contract_version"] or "")
        if contract_version not in BILLING_CONTRACT_VERSIONS:
            raise StoreConflict("video webhook contract version is invalid")
        material = "\0".join((contract_version, job_id, event_type, str(revision)))
        event_id = "evt_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        occurred_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        snapshot = self.snapshot(row)
        data = {
            "id": snapshot["job_id"],
            "request_id": snapshot["request_id"],
            "object": "video",
            "model": snapshot["model"],
            "status": snapshot["status"],
            "progress": 100 if snapshot["status"] in {"succeeded", "failed"} else 0,
            "created_at": int(snapshot.get("created_at") or 0),
            "completed_at": int(snapshot.get("finished_at") or 0) or None,
            "result_delivery": snapshot["result_delivery"],
            "result": None,
            "result_url": None,
            "billing": snapshot["billing"],
            "usage": None,
        }
        if isinstance(snapshot.get("input"), dict):
            data["input"] = snapshot["input"]
        if snapshot["result_delivery"] == "ready" and self.public_base_url:
            result_url = f"{self.public_base_url}/v1/videos/{job_id}/content"
            data["result"] = {"type": "url", "url": result_url}
            data["result_url"] = result_url
        payload = {
            "event_id": event_id,
            "event_version": 1,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "data": data,
        }
        current = int(time.time())
        connection.execute(
            """
            insert or ignore into video_webhook_outbox(
                event_id,job_id,event_type,event_revision,payload_json,status,
                attempts,next_attempt_at,created_at,updated_at
            ) values(?,?,?,?,?,'pending',0,?,?,?)
            """,
            (
                event_id,
                job_id,
                event_type,
                int(revision),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                current,
                current,
                current,
            ),
        )

    def due_webhook_events(self, *, limit: int = 20, lease_seconds: int = 30) -> list[dict[str, Any]]:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            rows = connection.execute(
                """
                select * from video_webhook_outbox
                where status in ('pending','retry','sending') and next_attempt_at<=?
                order by next_attempt_at,created_at limit ?
                """,
                (current, max(1, min(int(limit), 100))),
            ).fetchall()
            if rows:
                connection.executemany(
                    """
                    update video_webhook_outbox
                    set status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
                    where event_id=? and status in ('pending','retry','sending')
                    """,
                    [
                        (current + max(10, int(lease_seconds)), current, str(row["event_id"]))
                        for row in rows
                    ],
                )
                event_ids = [str(row["event_id"]) for row in rows]
                placeholders = ",".join("?" for _ in event_ids)
                rows = connection.execute(
                    f"select * from video_webhook_outbox where event_id in ({placeholders}) order by created_at",
                    event_ids,
                ).fetchall()
            connection.commit()
        return [dict(row) for row in rows]

    def mark_webhook_delivered(self, event_id: str) -> None:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """
                update video_webhook_outbox
                set status='delivered',last_error='',updated_at=?,delivered_at=?
                where event_id=? and status='sending'
                """,
                (current, current, event_id),
            )

    def retry_webhook(self, event_id: str, *, delay_seconds: int, error: str) -> None:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """
                update video_webhook_outbox
                set status='retry',next_attempt_at=?,last_error=?,updated_at=?
                where event_id=? and status='sending'
                """,
                (
                    current + max(5, min(int(delay_seconds), 3600)),
                    str(error or "webhook delivery failed")[:200],
                    current,
                    event_id,
                ),
            )

    def due_poll_jobs(self, *, limit: int = 50, lease_seconds: int = 30) -> list[dict[str, Any]]:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            rows = connection.execute(
                """
                select * from video_jobs
                where status='running' and upstream_task_id<>'' and next_poll_at<=?
                order by next_poll_at,updated_at limit ?
                """,
                (current, max(1, min(int(limit), 200))),
            ).fetchall()
            if rows:
                connection.executemany(
                    "update video_jobs set next_poll_at=?,poll_attempts=poll_attempts+1 where job_id=? and status='running'",
                    [(current + max(5, lease_seconds), row["job_id"]) for row in rows],
                )
            connection.commit()
        return [dict(row) for row in rows]

    def continue_running(
        self,
        job_id: str,
        *,
        upstream_status: str,
        poll_delay: int,
        error: dict[str, Any] | None = None,
        missing: bool = False,
    ) -> tuple[int, int]:
        current = int(time.time())
        error_json = json.dumps(error or {}, ensure_ascii=False, separators=(",", ":")) if error else ""
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select missing_count,missing_last_at,poll_errors from video_jobs where job_id=? and status='running'",
                (job_id,),
            ).fetchone()
            if not row:
                connection.commit()
                return 0, 0
            missing_count = int(row["missing_count"] or 0)
            missing_last_at = int(row["missing_last_at"] or 0)
            poll_errors = int(row["poll_errors"] or 0)
            if missing:
                if not missing_last_at or current - missing_last_at >= 10:
                    missing_count += 1
                    missing_last_at = current
            else:
                missing_count = 0
                missing_last_at = 0
            if error:
                poll_errors += 1
            else:
                poll_errors = 0
            connection.execute(
                """
                update video_jobs
                set upstream_status=?,error_json=?,poll_errors=?,missing_count=?,missing_last_at=?,
                    next_poll_at=?,updated_at=?
                where job_id=? and status='running'
                """,
                (
                    upstream_status[:80],
                    error_json,
                    poll_errors,
                    missing_count,
                    missing_last_at,
                    current + max(1, poll_delay),
                    current,
                    job_id,
                ),
            )
            connection.commit()
            return missing_count, poll_errors

    def active_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "select count(*) from video_jobs where status in ('queued','submitting','running','reconciling')"
            ).fetchone()
        return max(0, int(row[0] if row else 0))

    def uncertainty_snapshot(self, window_seconds: int) -> dict[str, Any]:
        cutoff = int(time.time()) - max(60, int(window_seconds))
        with self.connect() as connection:
            row = connection.execute(
                """
                select count(*) as terminal_count,
                       sum(case when status in ('uncertain','pending_review') then 1 else 0 end) as uncertain_count
                from video_jobs
                where status in ('succeeded','failed','uncertain','pending_review') and finished_at>=?
                """,
                (cutoff,),
            ).fetchone()
        terminal_count = max(0, int(row["terminal_count"] if row else 0))
        uncertain_count = max(0, int((row["uncertain_count"] if row else 0) or 0))
        return {
            "window_seconds": max(60, int(window_seconds)),
            "terminal_count": terminal_count,
            "uncertain_count": uncertain_count,
            "uncertainty_rate_percent": (uncertain_count * 100.0 / terminal_count) if terminal_count else 0.0,
        }

    def unhealthy_providers(
        self,
        *,
        failure_threshold: int = 3,
        window_seconds: int = 5 * 60,
    ) -> set[str]:
        """Return providers blocked by temporary health or persistent infrastructure quarantine."""
        current = int(time.time())
        cutoff = current - max(60, int(window_seconds))
        capacity_cutoff = current - GENERATION_CAPACITY_WINDOW_SECONDS
        counts: Counter[str] = Counter()
        capacity_failures: set[tuple[str, str]] = set()
        with self.connect() as connection:
            quarantined = {
                str(row["provider_id"] or "").strip().lower()
                for row in connection.execute(
                    """
                    select provider_id from video_provider_generation_quarantines
                    where status='active'
                    """
                ).fetchall()
            }
            rows = connection.execute(
                """
                select job_id,provider_id,status,error_json,upstream_task_id,
                       route_history_json,finished_at
                from video_jobs where updated_at>=?
                """,
                (min(cutoff, capacity_cutoff),),
            ).fetchall()
        for row in rows:
            try:
                history = json.loads(str(row["route_history_json"] or "[]"))
            except json.JSONDecodeError:
                history = []
            if isinstance(history, list):
                for entry in history:
                    if not isinstance(entry, dict) or int(entry.get("at") or 0) < cutoff:
                        continue
                    error = entry.get("error") if isinstance(entry.get("error"), dict) else {}
                    provider = str(entry.get("provider_id") or "").strip().lower()
                    occurred_at = int(entry.get("at") or 0)
                    if (
                        provider
                        and occurred_at >= capacity_cutoff
                        and not bool(error.get("uncertain", False))
                        and _is_generation_capacity_failure(error)
                    ):
                        capacity_failures.add((provider, str(row["job_id"])))
                    if (
                        provider
                        and occurred_at >= cutoff
                        and not bool(error.get("uncertain", False))
                    ):
                        counts[provider] += 1
            if row["status"] != "failed":
                continue
            error = _json_object(str(row["error_json"] or "")) or {}
            provider = str(row["provider_id"] or "").strip().lower()
            if (
                provider
                and int(row["finished_at"] or 0) >= capacity_cutoff
                and not bool(error.get("uncertain", False))
                and _is_generation_capacity_failure(error)
            ):
                capacity_failures.add((provider, str(row["job_id"])))
            if (
                provider
                and not str(row["upstream_task_id"] or "")
                and not bool(error.get("uncertain", False))
            ):
                counts[provider] += 1
        threshold = max(1, int(failure_threshold))
        capacity_counts = Counter(provider for provider, _ in capacity_failures)
        return quarantined | {
            provider for provider, count in counts.items() if count >= threshold
        } | {
            provider
            for provider, count in capacity_counts.items()
            if count >= GENERATION_CAPACITY_FAILURE_THRESHOLD
        }

    def generation_quarantines(self, *, active_only: bool = True) -> dict[str, dict[str, Any]]:
        """Return auditable provider generation quarantine state without credentials."""
        query = "select * from video_provider_generation_quarantines"
        if active_only:
            query += " where status='active'"
        query += " order by provider_id"
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return {
            str(row["provider_id"]): dict(row)
            for row in rows
            if str(row["provider_id"] or "")
        }

    def clear_generation_quarantine(self, provider_id: str, *, now: int | None = None) -> bool:
        """Explicitly clear a persistent generation quarantine and retain its audit row."""
        provider = str(provider_id or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", provider):
            raise ValueError("invalid provider id")
        current = int(time.time()) if now is None else int(now)
        with self.connect() as connection:
            connection.execute("begin immediate")
            cursor = connection.execute(
                """
                update video_provider_generation_quarantines
                set status='cleared',cleared_at=?
                where provider_id=? and status='active'
                """,
                (current, provider),
            )
            changed = cursor.rowcount == 1
            connection.commit()
        return changed

    def _record_generation_infrastructure_failure(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        provider_id: str,
        error: dict[str, Any],
        current: int,
    ) -> None:
        provider = str(provider_id or "").strip().lower()
        if not provider or not _is_generation_infrastructure_failure(error):
            return
        existing = connection.execute(
            """
            select * from video_provider_generation_quarantines
            where provider_id=?
            """,
            (provider,),
        ).fetchone()
        if existing and str(existing["status"] or "") == "active":
            connection.execute(
                """
                update video_provider_generation_quarantines
                set reason_code=?,failure_count=failure_count+1,last_failure_at=?
                where provider_id=?
                """,
                (_generation_failure_reason(error), current, provider),
            )
            return

        cutoff = current - GENERATION_QUARANTINE_WINDOW_SECONDS
        cleared_at = int(existing["cleared_at"] or 0) if existing else 0
        rows = connection.execute(
            """
            select error_json,finished_at from video_jobs
            where provider_id=? and job_id<>? and status='failed'
              and finished_at>=? and finished_at>?
            """,
            (provider, job_id, cutoff, cleared_at),
        ).fetchall()
        prior_failures = [
            row
            for row in rows
            if _is_generation_infrastructure_failure(
                _json_object(str(row["error_json"] or "")) or {}
            )
        ]
        failure_count = 1 + len(prior_failures)
        if failure_count < GENERATION_QUARANTINE_FAILURE_THRESHOLD:
            return
        first_failure_at = min(
            [current] + [int(row["finished_at"] or current) for row in prior_failures]
        )
        values = (
            "active",
            _generation_failure_reason(error),
            failure_count,
            GENERATION_QUARANTINE_WINDOW_SECONDS,
            first_failure_at,
            current,
            current,
            0,
            provider,
        )
        if existing:
            connection.execute(
                """
                update video_provider_generation_quarantines
                set status=?,reason_code=?,failure_count=?,window_seconds=?,
                    first_failure_at=?,last_failure_at=?,activated_at=?,cleared_at=?
                where provider_id=?
                """,
                values,
            )
        else:
            connection.execute(
                """
                insert into video_provider_generation_quarantines(
                    status,reason_code,failure_count,window_seconds,first_failure_at,
                    last_failure_at,activated_at,cleared_at,provider_id
                ) values(?,?,?,?,?,?,?,?,?)
                """,
                values,
            )

    def unhealthy_settlement_providers(
        self,
        *,
        min_age_seconds: int = 30 * 60,
        min_attempts: int = 3,
        now: int | None = None,
    ) -> set[str]:
        """Return providers whose successful tasks cannot close trusted billing.

        This signal is intentionally independent from submission health.  It is
        used only to stop new v2.1 work; existing settlement rows remain
        claimable by the billing monitor and therefore recover automatically.
        """
        current = int(time.time()) if now is None else int(now)
        cutoff = current - max(60, int(min_age_seconds))
        attempts = max(1, int(min_attempts))
        with self.connect() as connection:
            rows = connection.execute(
                """
                select distinct provider_id
                from video_jobs
                where status='succeeded'
                  and billing_status='settlement_pending'
                  and finished_at>0
                  and finished_at<=?
                  and settlement_query_attempts>=?
                  and provider_id<>''
                """,
                (cutoff, attempts),
            ).fetchall()
        return {
            str(row["provider_id"] or "").strip().lower()
            for row in rows
            if str(row["provider_id"] or "").strip()
        }

    def recover(self) -> list[str]:
        current = int(time.time())
        restart_error = json.dumps(
            {
                "code": "gateway_restart_during_submit",
                "category": "internal",
                "message": "中转站在上游提交期间重启；任务结果未知且不会自动重放。",
                "http_status": 503,
                "retryable": False,
                "uncertain": True,
                "phase": "submit",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self.connect() as connection:
            connection.execute("begin immediate")
            connection.execute(
                """
                update video_jobs
                set status='reconciling',error_json=?,updated_at=?,finished_at=0,next_poll_at=0,
                    recovery_started_at=case when recovery_started_at=0 then ? else recovery_started_at end,
                    recovery_deadline_at=case when recovery_deadline_at=0 then ? else recovery_deadline_at end,
                    recovery_next_at=?,billing_status=case when reserved_cny_exact<>'' then 'recovery_pending' else 'unavailable' end
                where status='submitting'
                """,
                (restart_error, current, current, current + 8 * 3600, current),
            )
            connection.execute(
                """
                update video_jobs
                set status='reconciling',error_json=?,updated_at=?,finished_at=0,next_poll_at=0,
                    recovery_started_at=case when recovery_started_at=0 then ? else recovery_started_at end,
                    recovery_deadline_at=case when recovery_deadline_at=0 then ? else recovery_deadline_at end,
                    recovery_next_at=?,billing_status=case when reserved_cny_exact<>'' then 'recovery_pending' else 'unavailable' end
                where status='running' and upstream_task_id=''
                """,
                (restart_error, current, current, current + 8 * 3600, current),
            )
            connection.execute(
                "update video_jobs set next_poll_at=? where status='running' and upstream_task_id<>''",
                (current,),
            )
            queued = [row[0] for row in connection.execute("select job_id from video_jobs where status='queued' order by created_at")]
            connection.commit()
        return queued

    def cleanup_expired(self, *, result_ttl_seconds: int, metadata_ttl_seconds: int) -> int:
        current = int(time.time())
        result_cutoff = current - max(3600, int(result_ttl_seconds))
        metadata_cutoff = current - max(int(result_ttl_seconds), int(metadata_ttl_seconds))
        with self.connect() as connection:
            connection.execute("begin immediate")
            connection.execute(
                """
                update video_jobs set payload_json='',result_json=''
                where status in ('succeeded','failed','uncertain','pending_review')
                  and finished_at>0 and finished_at<?
                """,
                (result_cutoff,),
            )
            expired_job_ids = [
                str(row[0])
                for row in connection.execute(
                    """
                    select job_id from video_jobs
                    where status in ('succeeded','failed','uncertain','pending_review')
                      and finished_at>0 and finished_at<?
                    """,
                    (metadata_cutoff,),
                ).fetchall()
            ]
            if expired_job_ids:
                placeholders = ",".join("?" for _ in expired_job_ids)
                connection.execute(
                    f"delete from video_webhook_outbox where job_id in ({placeholders})",
                    expired_job_ids,
                )
                connection.execute(
                    f"delete from video_settlements where job_id in ({placeholders})",
                    expired_job_ids,
                )
                connection.execute(
                    f"delete from video_provider_task_bindings where job_id in ({placeholders})",
                    expired_job_ids,
                )
            cursor = connection.execute(
                """
                delete from video_jobs
                where status in ('succeeded','failed','uncertain','pending_review')
                  and finished_at>0 and finished_at<?
                """,
                (metadata_cutoff,),
            )
            connection.commit()
        return max(0, int(cursor.rowcount or 0))


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _generation_failure_reason(error: dict[str, Any]) -> str:
    code = str(error.get("code") or "").strip().lower()
    message = " ".join(str(error.get("message") or "").lower().split())
    if any(marker in message for marker in GENERATION_INFRASTRUCTURE_MESSAGE_MARKERS):
        return "provider_credential_refresh_failed"
    return (code or "provider_generation_infrastructure_failed")[:80]


def _is_generation_infrastructure_failure(error: dict[str, Any]) -> bool:
    """Recognize only provider-side credential infrastructure failures.

    Generic provider failures, content rejection, and inaccessible customer
    media are deliberately excluded so they cannot quarantine a healthy route.
    """
    code = str(error.get("code") or "").strip().lower()
    category = str(error.get("category") or "").strip().lower()
    message = " ".join(str(error.get("message") or "").lower().split())
    if code in GENERATION_INFRASTRUCTURE_ERROR_CODES or category == "authentication":
        return True
    return any(marker in message for marker in GENERATION_INFRASTRUCTURE_MESSAGE_MARKERS)


def _is_generation_capacity_failure(error: dict[str, Any]) -> bool:
    """Recognize provider account-pool exhaustion without matching content errors."""
    code = str(error.get("code") or "").strip().lower()
    message = " ".join(str(error.get("message") or "").lower().split())
    if code in GENERATION_CAPACITY_ERROR_CODES:
        return True
    return any(marker in message for marker in GENERATION_CAPACITY_MESSAGE_MARKERS)


def _quantize_money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING), "f")


def _sum_attempt_costs(connection: sqlite3.Connection, job_id: str) -> Decimal:
    """Sum fixed-point text in Python Decimal; SQLite numeric affinity is binary float."""
    rows = connection.execute(
        "select actual_cost_cny_exact from video_job_attempts where job_id=? and actual_cost_cny_exact<>''",
        (job_id,),
    ).fetchall()
    total = Decimal("0")
    for row in rows:
        total += Decimal(_money_exact(row[0], allow_zero=True))
    if total > MONEY_LIMIT:
        raise StoreConflict("aggregate provider cost is outside the accepted range")
    return total


def _quantize_signed_money(value: Decimal) -> str:
    rounding = ROUND_CEILING if value >= 0 else ROUND_CEILING
    return format(value.quantize(MONEY_QUANTUM, rounding=rounding), "f")


def _money_exact(value: Any, *, allow_zero: bool) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise StoreConflict("billing money is invalid") from error
    if (
        not number.is_finite()
        or number < 0
        or (number == 0 and not allow_zero)
        or number > MONEY_LIMIT
    ):
        raise StoreConflict("billing money is outside the accepted range")
    return _quantize_money(number)


def _attempt_idempotency_key(row: sqlite3.Row | dict[str, Any]) -> str:
    request_id = str(row["request_id"] or "")
    provider_id = str(row["provider_id"] or "").strip().lower()
    route_index = int(row["route_index"] or 0)
    try:
        plan = json.loads(str(row["route_plan_json"] or "[]"))
    except json.JSONDecodeError:
        plan = []
    prior_same_provider = any(
        isinstance(previous, dict)
        and str(previous.get("provider_id") or "").strip().lower() == provider_id
        for previous in (plan[:route_index] if isinstance(plan, list) else [])
    )
    if not prior_same_provider:
        return request_id
    material = "\0".join(
        (
            request_id,
            provider_id,
            str(row["upstream_model"] or ""),
            str(row["adapter_revision"] or ""),
        )
    ).encode("utf-8")
    return "xtai-" + hashlib.sha256(material).hexdigest()[:48]


def _public_money(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return _money_exact(value, allow_zero=True)
    except StoreConflict:
        return None


def _reservation_from_payload(payload_json: str) -> dict[str, str]:
    unavailable = {
        "status": "unavailable",
        "reserved": "",
        "official": "",
        "revision": "",
        "contract_version": "",
    }
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return unavailable
    if not isinstance(payload, dict) or payload.get("_billing_v2") is not True:
        return unavailable
    contract_version = str(payload.get("_billing_contract_version") or "")
    if contract_version not in BILLING_CONTRACT_VERSIONS:
        return unavailable
    quote = payload.get("_relay_price") if isinstance(payload, dict) else None
    if not isinstance(quote, dict):
        return unavailable
    if (
        quote.get("contract_version") != PRICE_CONTRACT_VERSION
        or str(quote.get("currency") or "").upper() != "CNY"
        or str(quote.get("fallback_multiplier_exact") or "") != "1.5"
        or str(quote.get("price_source") or "") != "ark_official_1_5"
    ):
        return unavailable
    try:
        duration = int(payload.get("duration") or quote.get("output_seconds") or 0)
        reserved = Decimal(_money_exact(quote.get("amount_cny_exact"), allow_zero=False))
        official = Decimal(_money_exact(quote.get("official_cost_cny_exact"), allow_zero=False))
    except (TypeError, ValueError, InvalidOperation, StoreConflict):
        return unavailable
    if duration <= 0 or duration > 3600:
        return unavailable
    expected = Decimal(_quantize_money(official * Decimal("1.5")))
    if reserved != expected:
        return unavailable
    revision = str(quote.get("pricing_revision") or "").strip()[:160]
    if not revision:
        return unavailable
    return {
        "status": "reserved",
        "reserved": _quantize_money(reserved),
        "official": _quantize_money(official),
        "revision": revision,
        "contract_version": contract_version,
    }


def _evidence_fingerprint(evidence: dict[str, Any]) -> str:
    material = "\0".join(
        (
            evidence["contract_version"],
            evidence["job_id"],
            evidence["provider_task_id"],
            evidence["actual_cost_status"],
            evidence["actual_cost_cny_exact"],
            evidence["evidence_source"],
            evidence["evidence_id"],
            evidence["observed_at"],
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _provider_binding_fingerprint(binding: dict[str, Any]) -> str:
    material = "\0".join(
        (
            "xtai-video-provider-task-binding-v1",
            binding["job_id"],
            binding["provider_id"],
            binding["execution_task_id"],
            binding["billing_task_id"],
            binding["resolver_version"],
            binding["provider_record_id"],
            str(binding["provider_submit_time"]),
            str(binding["provider_finish_time"]),
            str(binding["media_size_bytes"]),
            binding["media_sha256"],
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validated_provider_binding(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StoreConflict("provider task binding must be an object")
    binding = {
        "job_id": str(payload.get("job_id") or "").strip(),
        "provider_id": str(payload.get("provider_id") or "").strip().lower(),
        "execution_task_id": str(payload.get("execution_task_id") or "").strip(),
        "billing_task_id": str(payload.get("billing_task_id") or "").strip(),
        "resolver_version": str(payload.get("resolver_version") or "").strip(),
        "provider_record_id": str(payload.get("provider_record_id") or "").strip(),
        "media_sha256": str(payload.get("media_sha256") or "").strip().lower(),
    }
    try:
        binding["provider_submit_time"] = int(payload.get("provider_submit_time"))
        binding["provider_finish_time"] = int(payload.get("provider_finish_time"))
        binding["media_size_bytes"] = int(payload.get("media_size_bytes"))
    except (TypeError, ValueError) as error:
        raise StoreConflict("provider task binding numeric evidence is invalid") from error
    if (
        not re.fullmatch(r"vjob_[0-9a-f]{32}", binding["job_id"])
        or not re.fullmatch(r"[a-z0-9_-]{1,40}", binding["provider_id"])
        or not binding["execution_task_id"]
        or len(binding["execution_task_id"]) > 200
        or not binding["billing_task_id"]
        or len(binding["billing_task_id"]) > 200
        or binding["execution_task_id"] == binding["billing_task_id"]
        or not re.fullmatch(r"[a-z0-9._-]{1,80}", binding["resolver_version"])
        or not binding["provider_record_id"]
        or len(binding["provider_record_id"]) > 200
        or binding["provider_submit_time"] <= 0
        or binding["provider_finish_time"] < binding["provider_submit_time"]
        or binding["media_size_bytes"] <= 0
        or binding["media_size_bytes"] > 2 * 1024 * 1024 * 1024
        or not SHA256_PATTERN.fullmatch(binding["media_sha256"])
    ):
        raise StoreConflict("provider task binding evidence is invalid")
    binding["evidence_fingerprint"] = _provider_binding_fingerprint(binding)
    return binding


def _settlement_id(job_id: str, revision: int, fingerprint: str) -> str:
    material = "\0".join(
        ("xtai-video-settlement-v2", job_id, str(revision), fingerprint)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_settlement_evidence(
    *,
    job_id: str,
    revision: int,
    provider_task_id: str,
    actual_cost_status: str,
    actual_cost_cny_exact: str,
    evidence_source: str,
    evidence_id: str,
    observed_at: str,
    contract_version: str = BILLING_CONTRACT_VERSION,
) -> dict[str, Any]:
    evidence = {
        "contract_version": str(contract_version or "").strip(),
        "job_id": str(job_id or "").strip(),
        "revision": int(revision),
        "provider_task_id": str(provider_task_id or "").strip(),
        "actual_cost_status": str(actual_cost_status or "").strip(),
        "actual_cost_cny_exact": str(actual_cost_cny_exact or "").strip(),
        "evidence_source": str(evidence_source or "").strip(),
        "evidence_id": str(evidence_id or "").strip(),
        "observed_at": str(observed_at or "").strip(),
    }
    evidence["evidence_fingerprint"] = _evidence_fingerprint(evidence)
    evidence["settlement_id"] = _settlement_id(
        evidence["job_id"], evidence["revision"], evidence["evidence_fingerprint"]
    )
    return _validated_settlement(evidence)


def _validated_settlement(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StoreConflict("settlement payload must be an object")
    contract_version = str(payload.get("contract_version") or "").strip()
    if contract_version not in BILLING_CONTRACT_VERSIONS:
        raise StoreConflict("settlement contract version is invalid")
    job_id = str(payload.get("job_id") or "").strip()
    provider_task_id = str(payload.get("provider_task_id") or "").strip()
    status = str(payload.get("actual_cost_status") or "").strip()
    source = str(payload.get("evidence_source") or "").strip()
    evidence_id = str(payload.get("evidence_id") or "").strip()
    observed_at = str(payload.get("observed_at") or "").strip()
    try:
        revision = int(payload.get("revision"))
    except (TypeError, ValueError) as error:
        raise StoreConflict("settlement revision is invalid") from error
    if (
        not re.fullmatch(r"vjob_[0-9a-f]{32}", job_id)
        or not provider_task_id
        or len(provider_task_id) > 200
        or revision <= 0
        or status not in {"actual", "zero_verified"}
        or source not in {
            "provider_account_ledger",
            "newapi_authenticated_video_task",
            "paisio_authenticated_request_ledger",
            "toonflow_web_operation_log",
            "xtai_aggregate_attempt_cost",
        }
        or not evidence_id
        or len(evidence_id) > 200
    ):
        raise StoreConflict("settlement evidence fields are invalid")
    amount = _money_exact(payload.get("actual_cost_cny_exact"), allow_zero=True)
    if (status == "actual" and Decimal(amount) <= 0) or (
        status == "zero_verified" and Decimal(amount) != 0
    ):
        raise StoreConflict("settlement status and amount disagree")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise StoreConflict("settlement observed_at is invalid") from error
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise StoreConflict("settlement observed_at must include a timezone")
    current = datetime.now(observed.tzinfo)
    if observed > current + timedelta(minutes=5) or observed < current - timedelta(days=30):
        raise StoreConflict("settlement observed_at is outside the approved window")
    evidence = {
        "contract_version": contract_version,
        "job_id": job_id,
        "provider_task_id": provider_task_id,
        "revision": revision,
        "actual_cost_status": status,
        "actual_cost_cny_exact": amount,
        "evidence_source": source,
        "evidence_id": evidence_id,
        "observed_at": observed_at,
    }
    fingerprint = str(payload.get("evidence_fingerprint") or "").strip().lower()
    settlement_id = str(payload.get("settlement_id") or "").strip().lower()
    if (
        not SHA256_PATTERN.fullmatch(fingerprint)
        or fingerprint != _evidence_fingerprint(evidence)
        or not SHA256_PATTERN.fullmatch(settlement_id)
        or settlement_id != _settlement_id(job_id, revision, fingerprint)
    ):
        raise StoreConflict("settlement fingerprint or id is invalid")
    evidence["evidence_fingerprint"] = fingerprint
    evidence["settlement_id"] = settlement_id
    return evidence


def _reference_items_digest(items: list[Any]) -> str:
    if not items:
        return ""
    canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reference_duration_total(items: list[Any]) -> str:
    total = Decimal("0")
    try:
        for item in items:
            if not isinstance(item, dict):
                return ""
            total += Decimal(str(item.get("duration_seconds") or "0"))
    except InvalidOperation:
        return ""
    return format(total.quantize(MONEY_QUANTUM), "f")


def _route_plan(
    raw: list[dict[str, Any]] | None,
    *,
    provider_id: str,
    upstream_model: str,
    adapter_revision: str,
) -> list[dict[str, Any]]:
    source = raw or [
        {
            "provider_id": provider_id,
            "upstream_model": upstream_model,
            "adapter_revision": adapter_revision,
            "send_resolution": False,
        }
    ]
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("video route plan entry must be an object")
        provider = str(item.get("provider_id") or "").strip().lower()
        model = str(item.get("upstream_model") or "").strip()
        revision = str(item.get("adapter_revision") or "").strip()
        route_key = (provider, model)
        if not provider or route_key in seen or not model or not revision:
            raise ValueError("video route plan is invalid or contains a duplicate provider/model")
        seen.add(route_key)
        result.append(
            {
                "provider_id": provider[:40],
                "upstream_model": model[:200],
                "adapter_revision": revision[:40],
                "send_resolution": bool(item.get("send_resolution", False)),
            }
        )
    if not result:
        raise ValueError("video route plan must not be empty")
    first = result[0]
    if (
        first["provider_id"] != str(provider_id or "").strip().lower()
        or first["upstream_model"] != str(upstream_model or "").strip()
        or first["adapter_revision"] != str(adapter_revision or "").strip()
    ):
        raise ValueError("video route plan first route does not match the selected route")
    return result
