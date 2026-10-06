"""Durable, single-submit image job gateway for the XingTu relay host.

The gateway is deliberately independent from New API. It persists an
idempotent job before starting one upstream request, returns a job id quickly,
and never starts a second upstream request for a repeated durable request id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DURABLE_REQUEST_ID_SUBMIT_CONTRACT = "durable-request-id-submit-v1"


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
JOB_ID_PATTERN = re.compile(r"^ijob_[0-9a-f]{32}$")
DATA_IMAGE_PATTERN = re.compile(r"^data:(image/(?:png|jpeg|jpg|webp));base64,(.+)$", re.I | re.S)
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
ACTIVE_STATUSES = {"queued", "submitting", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "uncertain"}


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    upstream_base_url: str
    upstream_api_key: str
    data_dir: Path
    listen_host: str = "0.0.0.0"
    listen_port: int = 8090
    max_request_bytes: int = 90 * 1024 * 1024
    max_response_bytes: int = 160 * 1024 * 1024
    max_reference_bytes: int = 20 * 1024 * 1024
    max_result_bytes: int = 100 * 1024 * 1024
    max_image_pixels: int = 64 * 1024 * 1024
    max_image_dimension: int = 16384
    max_active_jobs: int = 500
    worker_concurrency: int = 30
    upstream_timeout_seconds: int = 1350
    expected_upstream_timeout_seconds: int = 1200
    timeout_safety_margin_seconds: int = 150
    uncertainty_window_seconds: int = 10 * 60
    uncertainty_count_threshold: int = 2
    uncertainty_rate_percent: float = 1.0
    uncertainty_rate_min_samples: int = 20
    drain_file_name: str = "DRAIN"
    result_ttl_seconds: int = 3 * 24 * 60 * 60
    metadata_ttl_seconds: int = 30 * 24 * 60 * 60

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("IMAGE_JOB_GATEWAY_TOKEN", "").strip()
        upstream_api_key = os.getenv("IMAGE_JOB_UPSTREAM_API_KEY", "").strip()
        upstream_base_url = os.getenv("IMAGE_JOB_UPSTREAM_BASE_URL", "http://new-api:3000/v1").strip().rstrip("/")
        if not token:
            raise RuntimeError("IMAGE_JOB_GATEWAY_TOKEN is required")
        if not upstream_api_key:
            raise RuntimeError("IMAGE_JOB_UPSTREAM_API_KEY is required")
        parsed = urllib.parse.urlsplit(upstream_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("IMAGE_JOB_UPSTREAM_BASE_URL must be an HTTP(S) URL")
        result_ttl_seconds = max(
            3600,
            min(int(os.getenv("IMAGE_JOB_GATEWAY_RESULT_TTL_SECONDS", str(3 * 24 * 60 * 60))), 30 * 24 * 60 * 60),
        )
        metadata_ttl_seconds = max(
            result_ttl_seconds,
            min(int(os.getenv("IMAGE_JOB_GATEWAY_METADATA_TTL_SECONDS", str(30 * 24 * 60 * 60))), 90 * 24 * 60 * 60),
        )
        upstream_timeout_seconds = max(
            30,
            min(int(os.getenv("IMAGE_JOB_GATEWAY_UPSTREAM_TIMEOUT_SECONDS", "1350")), 1800),
        )
        expected_upstream_timeout_seconds = max(
            30,
            min(int(os.getenv("IMAGE_JOB_GATEWAY_EXPECTED_UPSTREAM_TIMEOUT_SECONDS", "1200")), 1800),
        )
        timeout_safety_margin_seconds = max(
            30,
            min(int(os.getenv("IMAGE_JOB_GATEWAY_TIMEOUT_SAFETY_MARGIN_SECONDS", "150")), 600),
        )
        if upstream_timeout_seconds < expected_upstream_timeout_seconds + timeout_safety_margin_seconds:
            raise RuntimeError(
                "IMAGE_JOB_GATEWAY_UPSTREAM_TIMEOUT_SECONDS must be at least "
                "IMAGE_JOB_GATEWAY_EXPECTED_UPSTREAM_TIMEOUT_SECONDS + "
                "IMAGE_JOB_GATEWAY_TIMEOUT_SAFETY_MARGIN_SECONDS"
            )
        drain_file_name = (os.getenv("IMAGE_JOB_GATEWAY_DRAIN_FILE_NAME", "DRAIN").strip() or "DRAIN")[:80]
        if Path(drain_file_name).name != drain_file_name or drain_file_name in {".", ".."}:
            raise RuntimeError("IMAGE_JOB_GATEWAY_DRAIN_FILE_NAME must be a plain file name")
        return cls(
            token=token,
            upstream_base_url=upstream_base_url,
            upstream_api_key=upstream_api_key,
            data_dir=Path(os.getenv("IMAGE_JOB_GATEWAY_DATA_DIR", "/data")).resolve(),
            listen_host=os.getenv("IMAGE_JOB_GATEWAY_HOST", "0.0.0.0").strip() or "0.0.0.0",
            listen_port=max(1, min(int(os.getenv("IMAGE_JOB_GATEWAY_PORT", "8090")), 65535)),
            max_request_bytes=max(1024 * 1024, min(int(os.getenv("IMAGE_JOB_GATEWAY_MAX_REQUEST_BYTES", str(90 * 1024 * 1024))), 200 * 1024 * 1024)),
            max_response_bytes=max(16 * 1024 * 1024, min(int(os.getenv("IMAGE_JOB_GATEWAY_MAX_RESPONSE_BYTES", str(160 * 1024 * 1024))), 200 * 1024 * 1024)),
            max_reference_bytes=max(1024 * 1024, min(int(os.getenv("IMAGE_JOB_GATEWAY_MAX_REFERENCE_BYTES", str(20 * 1024 * 1024))), 80 * 1024 * 1024)),
            max_result_bytes=max(1024 * 1024, min(int(os.getenv("IMAGE_JOB_BINARY_RESULT_MAX_BYTES", str(100 * 1024 * 1024))), 100 * 1024 * 1024)),
            max_image_pixels=max(1024 * 1024, min(int(os.getenv("IMAGE_JOB_BINARY_RESULT_MAX_PIXELS", str(64 * 1024 * 1024))), 100 * 1024 * 1024)),
            max_image_dimension=max(1024, min(int(os.getenv("IMAGE_JOB_BINARY_RESULT_MAX_DIMENSION", "16384")), 32768)),
            max_active_jobs=max(1, min(int(os.getenv("IMAGE_JOB_GATEWAY_MAX_ACTIVE_JOBS", "500")), 5000)),
            worker_concurrency=max(1, min(int(os.getenv("IMAGE_JOB_GATEWAY_WORKER_CONCURRENCY", "30")), 100)),
            upstream_timeout_seconds=upstream_timeout_seconds,
            expected_upstream_timeout_seconds=expected_upstream_timeout_seconds,
            timeout_safety_margin_seconds=timeout_safety_margin_seconds,
            uncertainty_window_seconds=max(60, min(int(os.getenv("IMAGE_JOB_GATEWAY_UNCERTAINTY_WINDOW_SECONDS", "600")), 3600)),
            uncertainty_count_threshold=max(1, min(int(os.getenv("IMAGE_JOB_GATEWAY_UNCERTAINTY_COUNT_THRESHOLD", "2")), 100)),
            uncertainty_rate_percent=max(0.1, min(float(os.getenv("IMAGE_JOB_GATEWAY_UNCERTAINTY_RATE_PERCENT", "1")), 100.0)),
            uncertainty_rate_min_samples=max(2, min(int(os.getenv("IMAGE_JOB_GATEWAY_UNCERTAINTY_RATE_MIN_SAMPLES", "20")), 1000)),
            drain_file_name=drain_file_name,
            result_ttl_seconds=result_ttl_seconds,
            metadata_ttl_seconds=metadata_ttl_seconds,
        )


class GatewayError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class ClosingConnection(sqlite3.Connection):
    """SQLite's default context commits, but does not close its connection."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, config: Config) -> None:
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = config.data_dir / "image-jobs.sqlite3"
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=ClosingConnection)
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
                create table if not exists image_jobs (
                    job_id text primary key,
                    request_id text not null unique,
                    fingerprint text not null,
                    status text not null,
                    mode text not null,
                    payload_json text not null,
                    result_json text not null default '',
                    result_files_json text not null default '',
                    error_code text not null default '',
                    error_message text not null default '',
                    upstream_request_id text not null default '',
                    created_at integer not null,
                    updated_at integer not null,
                    finished_at integer not null default 0
                );
                create index if not exists idx_image_jobs_status_updated
                    on image_jobs(status, updated_at);
                """
            )
            columns = {str(row[1]) for row in connection.execute("pragma table_info(image_jobs)")}
            if "result_files_json" not in columns:
                connection.execute("alter table image_jobs add column result_files_json text not null default ''")
            for name, default in (("phase", "legacy_unknown"), ("relay_request_id", ""), ("audit_json", "{}")):
                if name not in columns:
                    connection.execute(f"alter table image_jobs add column {name} text not null default '{default}'")

    @staticmethod
    def snapshot(row: sqlite3.Row, *, include_result: bool = True) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        if include_result and row["status"] == "succeeded" and row["result_json"]:
            try:
                parsed = json.loads(row["result_json"])
                result = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                result = None
        return {
            "job_id": row["job_id"],
            "request_id": row["request_id"],
            "status": row["status"],
            "upstream_request_id": row["upstream_request_id"],
            "relay_request_id": row["relay_request_id"],
            "phase": row["phase"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
            "error": (
                {"code": row["error_code"], "message": row["error_message"]}
                if row["error_code"]
                else None
            ),
            "result": result,
            "result_expired": bool(row["status"] == "succeeded" and row["finished_at"] and not row["result_json"]),
        }

    def create(self, request_id: str, fingerprint: str, mode: str, payload_json: str) -> tuple[dict[str, Any], bool]:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            existing = connection.execute("select * from image_jobs where request_id=?", (request_id,)).fetchone()
            if existing:
                connection.commit()
                if existing["fingerprint"] != fingerprint:
                    raise GatewayError(HTTPStatus.CONFLICT, "request_id_conflict", "request_id already belongs to another payload")
                return self.snapshot(existing), True
            active = connection.execute(
                "select count(*) from image_jobs where status in ('queued','submitting','running')"
            ).fetchone()[0]
            if int(active or 0) >= self.config.max_active_jobs:
                connection.rollback()
                raise GatewayError(HTTPStatus.TOO_MANY_REQUESTS, "gateway_queue_full", "image job gateway queue is full")
            job_id = f"ijob_{uuid.uuid4().hex}"
            payload = json.loads(payload_json)
            audit = {key: payload.get(key) for key in ("model", "size", "quality", "n")}
            connection.execute(
                "insert into image_jobs(job_id,request_id,fingerprint,status,mode,payload_json,phase,audit_json,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?)",
                (job_id, request_id, fingerprint, "queued", mode, payload_json, "queued", json.dumps(audit, separators=(",", ":")), current, current),
            )
            row = connection.execute("select * from image_jobs where job_id=?", (job_id,)).fetchone()
            connection.commit()
            return self.snapshot(row), False

    def active_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "select count(*) from image_jobs where status in ('queued','submitting','running')"
            ).fetchone()
        return max(0, int(row[0] if row else 0))

    def uncertainty_snapshot(self, window_seconds: int) -> dict[str, Any]:
        cutoff = int(time.time()) - max(60, int(window_seconds or 600))
        with self.connect() as connection:
            row = connection.execute(
                """
                select
                    count(*) as terminal_count,
                    sum(case when status='uncertain' then 1 else 0 end) as uncertain_count
                from image_jobs
                where status in ('succeeded','failed','uncertain') and finished_at>=?
                """,
                (cutoff,),
            ).fetchone()
        terminal_count = max(0, int(row["terminal_count"] if row else 0))
        uncertain_count = max(0, int((row["uncertain_count"] if row else 0) or 0))
        return {
            "window_seconds": max(60, int(window_seconds or 600)),
            "terminal_count": terminal_count,
            "uncertain_count": uncertain_count,
            "uncertainty_rate_percent": (uncertain_count * 100.0 / terminal_count) if terminal_count else 0.0,
        }

    def get(self, *, job_id: str = "", request_id: str = "") -> dict[str, Any] | None:
        with self.connect() as connection:
            if job_id:
                row = connection.execute("select * from image_jobs where job_id=?", (job_id,)).fetchone()
            else:
                row = connection.execute("select * from image_jobs where request_id=?", (request_id,)).fetchone()
            return self.snapshot(row) if row else None

    def claim(self, job_id: str) -> dict[str, Any] | None:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select * from image_jobs where job_id=?", (job_id,)).fetchone()
            if not row or row["status"] != "queued":
                connection.commit()
                return None
            connection.execute(
                "update image_jobs set status='submitting',phase='preparing',updated_at=? where job_id=? and status='queued'",
                (current, job_id),
            )
            row = connection.execute("select * from image_jobs where job_id=?", (job_id,)).fetchone()
            connection.commit()
            return dict(row)

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        result_files: list[dict[str, Any]] | None = None,
        code: str = "",
        message: str = "",
        upstream_request_id: str = "",
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        current = int(time.time())
        result_json = json.dumps(result or {}, ensure_ascii=False, separators=(",", ":")) if result else ""
        result_files_json = json.dumps(result_files or [], ensure_ascii=True, separators=(",", ":")) if result_files else ""
        with self.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute("select status from image_jobs where job_id=?", (job_id,)).fetchone()
            if row and row["status"] in ACTIVE_STATUSES:
                connection.execute(
                    "update image_jobs set status=?,result_json=?,result_files_json=?,error_code=?,error_message=?,upstream_request_id=coalesce(nullif(?,''),upstream_request_id),updated_at=?,finished_at=? where job_id=?",
                    (status, result_json, result_files_json, code[:80], message[:500], upstream_request_id[:160], current, current, job_id),
                )
            connection.commit()

    def phase(self, job_id: str, phase: str, *, relay_request_id: str = "", upstream_request_id: str = "") -> None:
        if phase not in {"preparing", "reference_fetch", "upstream_submit", "upstream_response", "result_processing"}:
            raise ValueError("invalid image execution phase")
        with self.connect() as connection:
            connection.execute(
                "update image_jobs set phase=?,relay_request_id=coalesce(nullif(?,''),relay_request_id),upstream_request_id=coalesce(nullif(?,''),upstream_request_id),updated_at=? where job_id=? and status in ('submitting','running')",
                (phase, relay_request_id[:160], upstream_request_id[:160], int(time.time()), job_id),
            )

    def result_file(self, job_id: str, index: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "select status,result_files_json from image_jobs where job_id=?",
                (job_id,),
            ).fetchone()
        if not row or row["status"] != "succeeded" or not row["result_files_json"]:
            return None
        try:
            files = json.loads(row["result_files_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(files, list) or index < 0 or index >= len(files) or not isinstance(files[index], dict):
            return None
        record = dict(files[index])
        relative = str(record.get("relative_path") or "").replace("\\", "/").strip("/")
        root = (self.config.data_dir / "results").resolve()
        path = (self.config.data_dir / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        if not path.is_file() or path.is_symlink():
            return None
        record["path"] = path
        return record

    def cleanup_expired(self) -> int:
        current = int(time.time())
        result_cutoff = current - self.config.result_ttl_seconds
        metadata_cutoff = current - self.config.metadata_ttl_seconds
        expired_files: list[dict[str, Any]] = []
        with self.connect() as connection:
            connection.execute("begin immediate")
            rows = connection.execute(
                "select job_id,result_files_json from image_jobs where status in ('succeeded','failed','uncertain') and finished_at>0 and finished_at<?",
                (result_cutoff,),
            ).fetchall()
            for row in rows:
                try:
                    values = json.loads(row["result_files_json"] or "[]")
                    if isinstance(values, list):
                        expired_files.extend(item for item in values if isinstance(item, dict))
                except json.JSONDecodeError:
                    pass
            connection.execute(
                "update image_jobs set payload_json='',result_json='',result_files_json='' where status in ('succeeded','failed','uncertain') and finished_at>0 and finished_at<?",
                (result_cutoff,),
            )
            connection.execute(
                "delete from image_jobs where status in ('succeeded','failed') and finished_at>0 and finished_at<?",
                (metadata_cutoff,),
            )
            connection.commit()
        removed = 0
        root = (self.config.data_dir / "results").resolve()
        for item in expired_files:
            relative = str(item.get("relative_path") or "").replace("\\", "/").strip("/")
            path = (self.config.data_dir / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
                removed += 1
            except OSError:
                pass
        return removed

    def recover(self) -> list[str]:
        current = int(time.time())
        with self.connect() as connection:
            connection.execute("begin immediate")
            connection.execute(
                "update image_jobs set status='failed',error_code='gateway_restart_before_submit',error_message='Gateway restarted before generation submission; no request was sent.',updated_at=?,finished_at=? where status in ('submitting','running') and phase in ('preparing','reference_fetch')",
                (current, current),
            )
            connection.execute(
                "update image_jobs set status='uncertain',error_code='gateway_restart_during_submit',error_message='Gateway restarted after upstream submission began; request was not replayed.',updated_at=?,finished_at=? where status in ('submitting','running')",
                (current, current),
            )
            queued = [row[0] for row in connection.execute("select job_id from image_jobs where status='queued' order by created_at")]
            connection.commit()
        self.cleanup_expired()
        return queued


class Gateway:
    def __init__(self, config: Config) -> None:
        self.config = config
        (config.data_dir / "temporary").mkdir(parents=True, exist_ok=True)
        (config.data_dir / "results").mkdir(parents=True, exist_ok=True)
        for part in (config.data_dir / "temporary").glob("*.part"):
            try:
                part.unlink()
            except OSError:
                pass
        self.store = Store(config)
        self.slots = threading.BoundedSemaphore(config.worker_concurrency)
        for job_id in self.store.recover():
            self.start(job_id)

    @property
    def drain_file(self) -> Path:
        return self.config.data_dir / self.config.drain_file_name

    def circuit_snapshot(self) -> dict[str, Any]:
        snapshot = self.store.uncertainty_snapshot(self.config.uncertainty_window_seconds)
        count_open = snapshot["uncertain_count"] >= self.config.uncertainty_count_threshold
        rate_open = (
            snapshot["terminal_count"] >= self.config.uncertainty_rate_min_samples
            and snapshot["uncertainty_rate_percent"] > self.config.uncertainty_rate_percent
        )
        return {
            **snapshot,
            "open": bool(count_open or rate_open),
            "count_threshold": self.config.uncertainty_count_threshold,
            "rate_threshold_percent": self.config.uncertainty_rate_percent,
            "rate_min_samples": self.config.uncertainty_rate_min_samples,
        }

    def readiness(self) -> tuple[bool, dict[str, Any]]:
        circuit = self.circuit_snapshot()
        draining = self.drain_file.exists()
        ready = not draining and not circuit["open"]
        return ready, {
            "ok": ready,
            "service": "image-job-gateway",
            "submit_replay_safe": True,
            "idempotency_contract": DURABLE_REQUEST_ID_SUBMIT_CONTRACT,
            "accepting": ready,
            "draining": draining,
            "active_jobs": self.store.active_count(),
            "circuit": circuit,
            "upstream_timeout_seconds": self.config.upstream_timeout_seconds,
            "expected_upstream_timeout_seconds": self.config.expected_upstream_timeout_seconds,
            "timeout_safety_margin_seconds": self.config.timeout_safety_margin_seconds,
            "time": int(time.time()),
        }

    @staticmethod
    def validate_payload(raw: Any) -> tuple[str, str, dict[str, Any], str]:
        if not isinstance(raw, dict):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "payload_invalid", "request body must be a JSON object")
        request_id = str(raw.get("request_id") or "").strip()
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "request_id_invalid", "request_id is invalid")
        model = str(raw.get("model") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not model or not prompt:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "image_payload_invalid", "model and prompt are required")
        references = raw.get("reference_images") or []
        if not isinstance(references, list) or len(references) > 3:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "reference_images_invalid", "reference_images must contain at most three items")
        normalized_refs: list[dict[str, str]] = []
        for item in references:
            if not isinstance(item, dict):
                raise GatewayError(HTTPStatus.BAD_REQUEST, "reference_image_invalid", "reference image item is invalid")
            url = str(item.get("url") or "").strip()
            if not (DATA_IMAGE_PATTERN.match(url) or url.startswith("https://")):
                raise GatewayError(HTTPStatus.BAD_REQUEST, "reference_image_url_invalid", "reference images must use data:image or HTTPS URLs")
            normalized_refs.append({"url": url, "role": str(item.get("role") or "reference")[:32]})
        payload = {
            "model": model,
            "prompt": prompt,
            "size": str(raw.get("size") or "1024x1024")[:40],
            "n": 1,
            "quality": str(raw.get("quality") or "")[:20],
            "reference_images": normalized_refs,
            "result_delivery_mode": (
                "binary_file" if str(raw.get("result_delivery_mode") or "").strip().lower() == "binary_file" else "b64_json"
            ),
        }
        mode = "edits" if normalized_refs else "generations"
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        return request_id, fingerprint, payload, mode

    def submit(self, raw: Any) -> tuple[dict[str, Any], bool]:
        self.store.cleanup_expired()
        request_id, fingerprint, payload, mode = self.validate_payload(raw)
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        existing = self.store.get(request_id=request_id)
        if existing:
            return self.store.create(request_id, fingerprint, mode, payload_json)
        if self.drain_file.exists():
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_draining", "image job gateway is draining and is not accepting new jobs")
        if self.circuit_snapshot()["open"]:
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_circuit_open", "image job gateway circuit is open after recent uncertain outcomes")
        snapshot, reused = self.store.create(request_id, fingerprint, mode, payload_json)
        if not reused and snapshot["status"] == "queued":
            self.start(snapshot["job_id"])
        return snapshot, reused

    def start(self, job_id: str) -> None:
        threading.Thread(target=self._run, args=(job_id,), daemon=True, name=f"image-job-{job_id[-8:]}").start()

    def _run(self, job_id: str) -> None:
        with self.slots:
            claimed = self.store.claim(job_id)
            if not claimed:
                return
            try:
                self.store.phase(job_id, "preparing")
                payload = json.loads(claimed["payload_json"])
                result, upstream_request_id, result_files = self._call_upstream(job_id, claimed["request_id"], claimed["mode"], payload)
                self.store.finish(
                    job_id,
                    "succeeded",
                    result=result,
                    result_files=result_files,
                    upstream_request_id=upstream_request_id,
                )
            except GatewayError as error:
                phase = (self.store.get(job_id=job_id) or {}).get("phase")
                submitted = phase in {"upstream_submit", "upstream_response", "result_processing"}
                self.store.finish(job_id, "uncertain" if submitted else "failed", code=error.code, message=str(error))
            except urllib.error.HTTPError as error:
                phase = (self.store.get(job_id=job_id) or {}).get("phase")
                if phase in {"preparing", "reference_fetch"}:
                    self.store.finish(job_id, "failed", code="reference_fetch_failed", message="Reference image could not be fetched; generation was not submitted")
                    return
                self.store.phase(job_id, "upstream_response", relay_request_id=error.headers.get("X-XingTu-Relay-Request-ID", ""), upstream_request_id=error.headers.get("X-Oneapi-Request-Id", "") )
                native_no_submit = bool(error.headers.get("X-XingTu-Relay-Request-ID")) and error.headers.get("X-XingTu-Image-Submission-State") in {"not_submitted", "rejected_no_task"}
                read_uncertain = False
                try:
                    body = error.read(4096).decode("utf-8", "replace")
                except (TimeoutError, urllib.error.URLError, ConnectionError, ssl.SSLError, OSError):
                    body = "Upstream error body could not be read; submission was not repeated"
                    read_uncertain = True
                finally:
                    error.close()
                try:
                    detail = json.loads(body).get("error", {})
                    uncertain = isinstance(detail, dict) and detail.get("code") == "image_submit_uncertain"
                except (ValueError, AttributeError):
                    detail = {}
                    uncertain = False
                definite_no_submit = isinstance(detail, dict) and detail.get("code") in {"model_not_found", "insufficient_user_quota", "pre_consume_token_quota_failed", "no_image_quota"}
                uncertain = (not native_no_submit) and (uncertain or read_uncertain or (not definite_no_submit and (error.code in {408,425} or error.code >= 500 or phase == "result_processing")))
                self.store.finish(job_id, "uncertain" if uncertain else "failed", code="upstream_submit_uncertain" if uncertain else f"upstream_http_{error.code}", message=body or str(error))
            except (TimeoutError, urllib.error.URLError, ConnectionError, ssl.SSLError) as error:
                phase = (self.store.get(job_id=job_id) or {}).get("phase")
                if phase in {"preparing", "reference_fetch"}:
                    self.store.finish(job_id, "failed", code="reference_fetch_failed", message="Reference image could not be fetched; generation was not submitted")
                    return
                self.store.finish(
                    job_id,
                    "uncertain",
                    code="upstream_submit_uncertain",
                    message=f"Upstream transport outcome is uncertain; request was not replayed: {type(error).__name__}",
                )
            except Exception as error:
                self.store.finish(
                    job_id,
                    "uncertain",
                    code="gateway_execution_uncertain",
                    message=f"Gateway execution ended without a confirmable outcome: {type(error).__name__}",
                )

    def _image_bytes(self, value: str) -> tuple[bytes, str, str]:
        match = DATA_IMAGE_PATTERN.match(value)
        if match:
            mime = match.group(1).lower().replace("image/jpg", "image/jpeg")
            try:
                content = base64.b64decode(match.group(2), validate=True)
            except Exception as error:
                raise GatewayError(HTTPStatus.BAD_REQUEST, "reference_image_invalid", "reference image base64 is invalid") from error
        else:
            request = urllib.request.Request(value, headers={"User-Agent": "XingTuImageJobGateway/1"})
            with urllib.request.urlopen(request, timeout=30) as response:
                mime = str(response.headers.get_content_type() or "").lower()
                if mime not in ALLOWED_IMAGE_TYPES:
                    raise GatewayError(HTTPStatus.BAD_REQUEST, "reference_image_type_invalid", "reference image content type is invalid")
                content = response.read(self.config.max_reference_bytes + 1)
        if not content or len(content) > self.config.max_reference_bytes:
            raise GatewayError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "reference_image_too_large", "reference image is too large")
        extension = mimetypes.guess_extension(mime) or ".png"
        if extension == ".jpe":
            extension = ".jpg"
        return content, mime, extension

    def _validate_result_image(self, path: Path) -> tuple[str, str, int, int]:
        size = path.stat().st_size
        if size <= 0 or size > self.config.max_result_bytes:
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_result_too_large", "upstream image result is empty or too large")
        with path.open("rb") as handle:
            head = handle.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24 and head[12:16] == b"IHDR":
                width, height = struct.unpack(">II", head[16:24])
                mime, extension = "image/png", "png"
                handle.seek(-12, os.SEEK_END)
                if handle.read(12) != b"\x00\x00\x00\x00IEND\xaeB`\x82":
                    raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_invalid", "PNG result is incomplete")
            elif head.startswith(b"\xff\xd8"):
                mime, extension = "image/jpeg", "jpg"
                handle.seek(-2, os.SEEK_END)
                if handle.read(2) != b"\xff\xd9":
                    raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_invalid", "JPEG result is incomplete")
                handle.seek(2)
                width = height = 0
                while handle.tell() < min(size, 2 * 1024 * 1024):
                    marker_start = handle.read(1)
                    if not marker_start:
                        break
                    if marker_start != b"\xff":
                        continue
                    marker = handle.read(1)
                    while marker == b"\xff":
                        marker = handle.read(1)
                    if not marker or marker in {b"\xd8", b"\xd9"}:
                        continue
                    length_raw = handle.read(2)
                    if len(length_raw) != 2:
                        break
                    segment_length = struct.unpack(">H", length_raw)[0]
                    if segment_length < 2:
                        break
                    if marker[0] in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                        data = handle.read(5)
                        if len(data) == 5:
                            height, width = struct.unpack(">HH", data[1:5])
                        break
                    handle.seek(segment_length - 2, os.SEEK_CUR)
                if not width or not height:
                    raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_invalid", "JPEG dimensions are missing")
            elif head.startswith(b"RIFF") and head[8:12] == b"WEBP":
                declared = struct.unpack("<I", head[4:8])[0] + 8
                if declared != size:
                    raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_invalid", "WebP result is incomplete")
                mime, extension = "image/webp", "webp"
                chunk = head[12:16]
                if chunk == b"VP8X" and len(head) >= 30:
                    width = 1 + int.from_bytes(head[24:27], "little")
                    height = 1 + int.from_bytes(head[27:30], "little")
                elif chunk == b"VP8L" and len(head) >= 25 and head[20] == 0x2F:
                    bits = int.from_bytes(head[21:25], "little")
                    width = (bits & 0x3FFF) + 1
                    height = ((bits >> 14) & 0x3FFF) + 1
                elif chunk == b"VP8 " and len(head) >= 30 and head[23:26] == b"\x9d\x01\x2a":
                    width = struct.unpack("<H", head[26:28])[0] & 0x3FFF
                    height = struct.unpack("<H", head[28:30])[0] & 0x3FFF
                else:
                    raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_invalid", "WebP dimensions are missing")
            else:
                raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_type_invalid", "upstream image type is not PNG, JPEG, or WebP")
        if (
            width <= 0
            or height <= 0
            or width > self.config.max_image_dimension
            or height > self.config.max_image_dimension
            or width * height > self.config.max_image_pixels
        ):
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_dimensions_invalid", "upstream image dimensions exceed the safe limit")
        return mime, extension, width, height

    def _persist_result_value(self, job_id: str, index: int, value: str, *, is_base64: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        temporary = self.config.data_dir / "temporary" / f"{job_id}-{index}-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as handle:
                if is_base64:
                    encoded = value.strip()
                    estimated = (len(encoded) // 4) * 3
                    if not encoded or estimated > self.config.max_result_bytes + 2:
                        raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_result_too_large", "upstream image result is empty or too large")
                    block = 4 * 256 * 1024
                    for offset in range(0, len(encoded), block):
                        try:
                            chunk = base64.b64decode(encoded[offset:offset + block], validate=True)
                        except Exception as error:
                            raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_base64_invalid", "upstream image base64 is invalid") from error
                        written += len(chunk)
                        if written > self.config.max_result_bytes:
                            raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_result_too_large", "upstream image result is too large")
                        digest.update(chunk)
                        handle.write(chunk)
                else:
                    request = urllib.request.Request(value, headers={"User-Agent": "XingTuImageJobGateway/1"})
                    with urllib.request.urlopen(request, timeout=60) as response:
                        while True:
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > self.config.max_result_bytes:
                                raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_result_too_large", "upstream image result is too large")
                            digest.update(chunk)
                            handle.write(chunk)
            mime, extension, width, height = self._validate_result_image(temporary)
            result_dir = self.config.data_dir / "results" / job_id
            result_dir.mkdir(parents=True, exist_ok=True)
            final_path = result_dir / f"image_{index}.{extension}"
            os.replace(temporary, final_path)
            sha256_hex = digest.hexdigest()
            public = {
                "type": "binary_file",
                "file_id": f"image_{index}",
                "content_type": mime,
                "size_bytes": written,
                "sha256": sha256_hex,
                "width": width,
                "height": height,
                "download_path": f"/v1/image-jobs/{job_id}/result-file/{index}",
            }
            internal = {
                **public,
                "relative_path": final_path.relative_to(self.config.data_dir).as_posix(),
            }
            return public, internal
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _multipart(fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
        boundary = f"xtai-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        for name, filename, content_type, content in files:
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def _call_upstream(self, job_id: str, request_id: str, mode: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        headers = {
            "Authorization": f"Bearer {self.config.upstream_api_key}",
            "Accept": "application/json",
            "Idempotency-Key": request_id,
            "X-Request-ID": request_id,
            "User-Agent": "XingTuImageJobGateway/1",
        }
        if mode == "generations":
            body = json.dumps(
                {
                    "model": payload["model"],
                    "prompt": payload["prompt"],
                    "size": payload["size"],
                    "n": 1,
                    "response_format": "b64_json",
                    **({"quality": payload["quality"]} if payload.get("quality") in {"low", "medium", "high"} else {}),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            self.store.phase(job_id, "reference_fetch")
            files: list[tuple[str, str, str, bytes]] = []
            for index, item in enumerate(payload.get("reference_images") or [], 1):
                content, mime, extension = self._image_bytes(str(item.get("url") or ""))
                files.append(("image", f"reference_{index}{extension}", mime, content))
            fields = {
                "model": payload["model"],
                "prompt": payload["prompt"],
                "size": payload["size"],
                "n": "1",
                "response_format": "b64_json",
            }
            if payload.get("quality") in {"low", "medium", "high"}:
                fields["quality"] = payload["quality"]
            body, content_type = self._multipart(fields, files)
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            f"{self.config.upstream_base_url}/images/{mode}",
            data=body,
            headers=headers,
            method="POST",
        )
        self.store.phase(job_id, "upstream_submit")
        with urllib.request.urlopen(request, timeout=self.config.upstream_timeout_seconds) as response:
            # Save headers BEFORE a body read / result download can fail.
            self.store.phase(job_id, "upstream_response", relay_request_id=response.headers.get("X-XingTu-Relay-Request-ID", ""), upstream_request_id=response.headers.get("X-Oneapi-Request-Id", ""))
            raw_body = response.read(self.config.max_response_bytes + 1)
            if len(raw_body) > self.config.max_response_bytes:
                raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_result_too_large", "upstream image result is too large")
            raw = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            upstream_request_id = next(
                (str(response.headers.get(name) or "").strip() for name in ("x-request-id", "x-oneapi-request-id", "request-id") if response.headers.get(name)),
                "",
            )
        items = raw.get("data") if isinstance(raw, dict) else None
        self.store.phase(job_id, "result_processing")
        if isinstance(items, dict):
            items = items.get("data")
        images: list[dict[str, Any]] = []
        result_files: list[dict[str, Any]] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("b64_json"):
                    if payload.get("result_delivery_mode") == "binary_file":
                        public, internal = self._persist_result_value(job_id, len(images), str(item["b64_json"]), is_base64=True)
                        images.append(public)
                        result_files.append(internal)
                    else:
                        images.append({"type": "b64", "value": str(item["b64_json"])})
                elif item.get("url"):
                    # URL results stay on the existing cloud-side safe-fetch path.
                    # The gateway only persists inline base64 outputs, avoiding a
                    # second independent SSRF/redirect trust boundary here.
                    images.append({"type": "url", "value": str(item["url"])})
        if not images:
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "upstream_image_empty", "upstream completed without an image")
        return {
            "ok": True,
            "images": images,
            "model": str(raw.get("model") or payload["model"]),
            "provider": "relay_image_job_gateway",
            "upstream_request_id": upstream_request_id,
            "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
        }, upstream_request_id, result_files


def handler_class(gateway: Gateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "XingTuImageJobGateway/1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def json_response(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            expected = f"Bearer {gateway.config.token}"
            supplied = self.headers.get("Authorization", "")
            return bool(supplied) and hmac.compare_digest(supplied, expected)

        def file_response(self, job_id: str, index: int, *, head_only: bool = False) -> bool:
            record = gateway.store.result_file(job_id, index)
            if not record:
                return False
            path = record["path"]
            size = int(record.get("size_bytes") or path.stat().st_size)
            if size != path.stat().st_size or size <= 0:
                return False
            start, end = 0, size - 1
            status = HTTPStatus.OK
            range_header = str(self.headers.get("Range") or "").strip()
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                if not match or (not match.group(1) and not match.group(2)):
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return True
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else size - 1
                else:
                    suffix = int(match.group(2))
                    start = max(0, size - suffix)
                    end = size - 1
                if start >= size or end < start:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return True
                end = min(end, size - 1)
                status = HTTPStatus.PARTIAL_CONTENT
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", str(record.get("content_type") or "application/octet-stream"))
            self.send_header("Content-Length", str(length))
            self.send_header("ETag", f'"{str(record.get("sha256") or "")}"')
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return True
            remaining = length
            with path.open("rb") as handle:
                handle.seek(start)
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return True

        def serve_get_or_head(self, *, head_only: bool = False) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/health":
                self.json_response(HTTPStatus.OK, {
                    "ok": True,
                    "service": "image-job-gateway",
                    "submit_replay_safe": True,
                    "idempotency_contract": DURABLE_REQUEST_ID_SUBMIT_CONTRACT,
                    "time": int(time.time()),
                })
                return
            if parsed.path == "/ready":
                ready, payload = gateway.readiness()
                self.json_response(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE, payload)
                return
            if not self.authorized():
                self.json_response(HTTPStatus.UNAUTHORIZED, {"error": {"code": "unauthorized", "message": "unauthorized"}})
                return
            file_match = re.fullmatch(r"/v1/image-jobs/(ijob_[0-9a-f]{32})/result-file/(\d+)", parsed.path)
            if file_match:
                if self.file_response(file_match.group(1), int(file_match.group(2)), head_only=head_only):
                    return
                self.json_response(HTTPStatus.NOT_FOUND, {"error": {"code": "result_file_not_found", "message": "result file was not found"}})
                return
            if head_only:
                self.json_response(HTTPStatus.NOT_FOUND, {"error": {"code": "not_found", "message": "not found"}})
                return
            snapshot = None
            if parsed.path.startswith("/v1/image-jobs/by-request/"):
                request_id = urllib.parse.unquote(parsed.path.removeprefix("/v1/image-jobs/by-request/"))
                if REQUEST_ID_PATTERN.fullmatch(request_id):
                    snapshot = gateway.store.get(request_id=request_id)
            elif parsed.path.startswith("/v1/image-jobs/"):
                job_id = parsed.path.removeprefix("/v1/image-jobs/")
                if JOB_ID_PATTERN.fullmatch(job_id):
                    snapshot = gateway.store.get(job_id=job_id)
            if snapshot is None:
                self.json_response(HTTPStatus.NOT_FOUND, {"error": {"code": "job_not_found", "message": "image job was not found"}})
                return
            self.json_response(HTTPStatus.OK, {"ok": True, "job": snapshot})

        def do_GET(self) -> None:
            self.serve_get_or_head()

        def do_HEAD(self) -> None:
            self.serve_get_or_head(head_only=True)

        def do_POST(self) -> None:
            if urllib.parse.urlsplit(self.path).path != "/v1/image-jobs":
                self.json_response(HTTPStatus.NOT_FOUND, {"error": {"code": "not_found", "message": "not found"}})
                return
            if not self.authorized():
                self.json_response(HTTPStatus.UNAUTHORIZED, {"error": {"code": "unauthorized", "message": "unauthorized"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > gateway.config.max_request_bytes:
                self.json_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": {"code": "request_too_large", "message": "request body size is invalid"}})
                return
            try:
                raw = json.loads(self.rfile.read(length).decode("utf-8"))
                snapshot, reused = gateway.submit(raw)
                self.json_response(HTTPStatus.ACCEPTED, {"ok": True, "reused": reused, "job": snapshot})
            except GatewayError as error:
                self.json_response(error.status, {"error": {"code": error.code, "message": str(error)}})
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.json_response(HTTPStatus.BAD_REQUEST, {"error": {"code": "json_invalid", "message": "request body is not valid JSON"}})

    return Handler


def main() -> None:
    config = Config.from_env()
    gateway = Gateway(config)
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), handler_class(gateway))
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
