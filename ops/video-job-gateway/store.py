"""SQLite WAL state store for the XingTu video relay."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"queued", "submitting", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "uncertain", "pending_review"}
BILLING_CONTRACT_LEGACY = "xtai-video-billing-v2"
BILLING_CONTRACT_VERSION = "xtai-video-billing-v2.1"
SUPPORTED_BILLING_CONTRACT_VERSIONS = {
    BILLING_CONTRACT_LEGACY,
    BILLING_CONTRACT_VERSION,
}
PRICE_CONTRACT_VERSION = "xtai-video-pricing-v1"
MONEY_QUANTUM = Decimal("0.000001")
MONEY_LIMIT = Decimal("100000")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class StoreConflict(ValueError):
    pass


class Store:
    def __init__(self, data_dir: Path, *, max_active_jobs: int = 500) -> None:
        self.data_dir = data_dir
        self.max_active_jobs = max(1, min(int(max_active_jobs), 5000))
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
                    poll_attempts integer not null default 0,
                    poll_errors integer not null default 0,
                    missing_count integer not null default 0,
                    missing_last_at integer not null default 0,
                    next_poll_at integer not null default 0,
                    billing_contract_version text not null default 'xtai-video-billing-v2.1',
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
                "billing_contract_version": "text not null default 'xtai-video-billing-v2.1'",
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
            }
            for name, definition in migrations.items():
                if name not in existing_columns:
                    connection.execute(f"alter table video_jobs add column {name} {definition}")

    @staticmethod
    def snapshot(row: sqlite3.Row | dict[str, Any], *, include_result: bool = True) -> dict[str, Any]:
        source = dict(row)
        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        billing_status = str(source.get("billing_status") or "unavailable")
        if billing_status == "settled_with_debt":
            billing_status = "payment_required"
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
            "contract_version": str(
                source.get("billing_contract_version") or BILLING_CONTRACT_VERSION
            ),
            "status": billing_status,
            "currency": "CNY",
            "reserved_amount": _public_money(source.get("reserved_cny_exact")),
            "charged_amount": _public_money(source.get("charged_cny_exact")),
            "refund_amount": _public_money(source.get("refund_cny_exact")),
            "supplement_amount": _public_money(source.get("supplement_cny_exact")),
            "settlement_revision": int(source.get("settlement_revision") or 0),
        }
        status = str(source.get("status") or "")
        if status == "succeeded":
            delivery = "ready" if result_ready else "pending_settlement"
        else:
            delivery = "unavailable"
        return {
            "job_id": source.get("job_id"),
            "request_id": source.get("request_id"),
            "protocol_version": source.get("protocol_version"),
            "catalog_revision": source.get("catalog_revision"),
            "model": source.get("stable_model"),
            "status": source.get("status"),
            "created_at": int(source.get("created_at") or 0),
            "updated_at": int(source.get("updated_at") or 0),
            "finished_at": int(source.get("finished_at") or 0),
            "error": error,
            "result": result,
            "result_delivery": delivery,
            "billing": billing,
            "result_expired": bool(
                source.get("status") == "succeeded"
                and result_ready
                and source.get("finished_at")
                and not source.get("result_json")
            ),
        }

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
                "select count(*) from video_jobs where status in ('queued','submitting','running')"
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
                    payload_json,billing_status,reserved_cny_exact,official_cost_cny_exact,
                    official_pricing_revision,billing_markup_exact,created_at,updated_at
                ) values(?,?,?,?,?,?,?,?,?,?,0,?,'[]','queued',?,?,?,?,?,?,?,?)
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
            connection.execute(
                """
                update video_jobs
                set status='submitting',submit_attempts=submit_attempts+1,updated_at=?
                where job_id=? and status='queued'
                """,
                (current, job_id),
            )
            row = connection.execute("select * from video_jobs where job_id=?", (job_id,)).fetchone()
            connection.commit()
            return dict(row)

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
                    next_poll_at=?,updated_at=?
                where job_id=? and status='submitting'
                """,
                (upstream_task_id[:200], upstream_status[:80], current + max(1, poll_delay), current, job_id),
            )
            connection.commit()

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        upstream_task_id: str = "",
        upstream_status: str = "",
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal video job status")
        current = int(time.time())
        result_json = json.dumps(result or {}, ensure_ascii=False, separators=(",", ":")) if result else ""
        error_json = json.dumps(error or {}, ensure_ascii=False, separators=(",", ":")) if error else ""
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select status,upstream_task_id from video_jobs where job_id=?", (job_id,)).fetchone()
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
                        billing_status=?,charged_cny_exact=?,refund_cny_exact=?,
                        supplement_cny_exact='',
                        next_poll_at=0,updated_at=?,finished_at=?
                    where job_id=?
                    """,
                    (
                        status,
                        result_json,
                        error_json,
                        task_id[:200],
                        upstream_status[:80],
                        billing_status,
                        charged,
                        refund,
                        current,
                        current,
                        job_id,
                    ),
                )
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
            if str(row["status"]) != "succeeded":
                connection.rollback()
                raise StoreConflict("only a succeeded video job can be settled")
            if str(row["billing_contract_version"]) != evidence["contract_version"]:
                connection.rollback()
                raise StoreConflict("settlement contract does not match the video job")
            if str(row["billing_status"]) not in {"settlement_pending", "settled"}:
                connection.rollback()
                raise StoreConflict("video job has no settleable reservation")
            if str(row["upstream_task_id"]) != evidence["provider_task_id"]:
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
                    updated_at=?
                where job_id=? and status='succeeded'
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
            connection.commit()
            return self.snapshot(updated), False

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
                "select count(*) from video_jobs where status in ('queued','submitting','running')"
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
        """Return providers with repeated definite pre-creation failures in the recent window."""
        cutoff = int(time.time()) - max(60, int(window_seconds))
        counts: Counter[str] = Counter()
        with self.connect() as connection:
            rows = connection.execute(
                """
                select provider_id,status,error_json,upstream_task_id,route_history_json
                from video_jobs where updated_at>=?
                """,
                (cutoff,),
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
                    if provider and not bool(error.get("uncertain", False)):
                        counts[provider] += 1
            if row["status"] != "failed" or str(row["upstream_task_id"] or ""):
                continue
            error = _json_object(str(row["error_json"] or "")) or {}
            provider = str(row["provider_id"] or "").strip().lower()
            if provider and not bool(error.get("uncertain", False)):
                counts[provider] += 1
        threshold = max(1, int(failure_threshold))
        return {provider for provider, count in counts.items() if count >= threshold}

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
                set status='uncertain',error_json=?,updated_at=?,finished_at=?,next_poll_at=0
                where status='submitting'
                """,
                (restart_error, current, current),
            )
            connection.execute(
                """
                update video_jobs
                set status='pending_review',error_json=?,updated_at=?,finished_at=?,next_poll_at=0
                where status='running' and upstream_task_id=''
                """,
                (restart_error, current, current),
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


def _quantize_money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING), "f")


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


def _public_money(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return _money_exact(value, allow_zero=True)
    except StoreConflict:
        return None


def _reservation_from_payload(payload_json: str) -> dict[str, str]:
    unavailable = {"status": "unavailable", "reserved": "", "official": "", "revision": ""}
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
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


def _settlement_id(job_id: str, revision: int, fingerprint: str) -> str:
    material = "\0".join(
        ("xtai-video-settlement-v2", job_id, str(revision), fingerprint)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validated_settlement(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StoreConflict("settlement payload must be an object")
    contract_version = str(payload.get("contract_version") or "")
    if contract_version not in SUPPORTED_BILLING_CONTRACT_VERSIONS:
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
            "toonflow_web_operation_log",
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
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("video route plan entry must be an object")
        provider = str(item.get("provider_id") or "").strip().lower()
        model = str(item.get("upstream_model") or "").strip()
        revision = str(item.get("adapter_revision") or "").strip()
        if not provider or provider in seen or not model or not revision:
            raise ValueError("video route plan is invalid or contains a duplicate provider")
        seen.add(provider)
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
