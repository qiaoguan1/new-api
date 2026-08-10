"""SQLite WAL state store for the XingTu video relay."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"queued", "submitting", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "uncertain", "pending_review"}


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
            }
            for name, definition in migrations.items():
                if name not in existing_columns:
                    connection.execute(f"alter table video_jobs add column {name} {definition}")

    @staticmethod
    def snapshot(row: sqlite3.Row | dict[str, Any], *, include_result: bool = True) -> dict[str, Any]:
        source = dict(row)
        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        if include_result and source.get("status") == "succeeded" and source.get("result_json"):
            result = _json_object(str(source.get("result_json") or ""))
        if source.get("error_json"):
            error = _json_object(str(source.get("error_json") or ""))
        return {
            "job_id": source.get("job_id"),
            "request_id": source.get("request_id"),
            "protocol_version": source.get("protocol_version"),
            "catalog_revision": source.get("catalog_revision"),
            "model": source.get("stable_model"),
            "status": source.get("status"),
            "upstream_status": source.get("upstream_status") or "",
            "created_at": int(source.get("created_at") or 0),
            "updated_at": int(source.get("updated_at") or 0),
            "finished_at": int(source.get("finished_at") or 0),
            "error": error,
            "result": result,
            "result_expired": bool(source.get("status") == "succeeded" and source.get("finished_at") and not source.get("result_json")),
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
                    payload_json,created_at,updated_at
                ) values(?,?,?,?,?,?,?,?,?,?,0,?,'[]','queued',?,?,?)
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
                connection.execute(
                    """
                    update video_jobs
                    set status=?,result_json=?,error_json=?,upstream_task_id=?,upstream_status=?,
                        next_poll_at=0,updated_at=?,finished_at=?
                    where job_id=?
                    """,
                    (status, result_json, error_json, task_id[:200], upstream_status[:80], current, current, job_id),
                )
            connection.commit()

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
