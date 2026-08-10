"""Durable, provider-adapted video job gateway for the XingTu relay host.

The service is a sidecar. It does not modify New API and receives no traffic
until the XingTu cloud feature flag is enabled. JSON control traffic passes
through the gateway; HTTPS inputs and result URLs remain direct whenever the
selected upstream supports them.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from adapters import AdapterError, PaisioAdapter, ProviderConfig, RollDekAdapter, ToonflowAdapter, VideoAdapter
from catalog import Catalog, CatalogError, Model, Route
from relay_pricing import PRICE_CONTRACT_VERSION, RelayPricing, RelayPricingError
from routing import RoutePlanError, build_route_plan
from store import Store, StoreConflict


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
JOB_ID_PATTERN = re.compile(r"^vjob_[0-9a-f]{32}$")
DURABLE_REQUEST_ID_SUBMIT_CONTRACT = "durable-request-id-submit-v1"
ALLOWED_MODES = {"text", "first_frame", "first_last_frame", "reference", "all_reference"}
ALLOWED_IMAGE_ROLES = {"reference", "first", "last", "style"}
ALLOWED_VIDEO_ROLES = {"reference", "camera_motion"}


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    data_dir: Path
    catalog_file: Path
    providers: dict[str, ProviderConfig]
    pricing_url: str = ""
    pricing_file: Path | None = None
    pricing_group: str = "视频"
    pricing_timeout_seconds: int = 5
    pricing_cache_seconds: int = 30
    listen_host: str = "0.0.0.0"
    listen_port: int = 8091
    max_request_bytes: int = 256 * 1024
    max_active_jobs: int = 500
    submit_concurrency: int = 20
    poll_concurrency: int = 50
    stream_concurrency: int = 4
    poll_interval_seconds: int = 8
    max_job_age_seconds: int = 8 * 60 * 60
    uncertainty_window_seconds: int = 10 * 60
    uncertainty_count_threshold: int = 2
    uncertainty_rate_percent: float = 1.0
    uncertainty_rate_min_samples: int = 20
    drain_file_name: str = "DRAIN"
    result_ttl_seconds: int = 3 * 24 * 60 * 60
    metadata_ttl_seconds: int = 30 * 24 * 60 * 60
    max_stream_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("VIDEO_JOB_GATEWAY_TOKEN", "").strip()
        if not token:
            raise RuntimeError("VIDEO_JOB_GATEWAY_TOKEN is required")
        data_dir = Path(os.getenv("VIDEO_JOB_GATEWAY_DATA_DIR", "/data")).resolve()
        catalog_file = Path(
            os.getenv("VIDEO_JOB_GATEWAY_CATALOG_FILE", str(Path(__file__).with_name("catalog.json")))
        ).resolve()
        enabled = {
            value.strip().lower()
            for value in os.getenv("VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS", "").split(",")
            if value.strip().lower() in {"paisio", "rolldek", "toonflow"}
        }
        providers: dict[str, ProviderConfig] = {}
        provider_defaults = {
            "paisio": ("https://api.paisio.online", "api.paisio.online,cdn.paisio.online"),
            "rolldek": ("https://rolldek.com", "rolldek.com"),
            "toonflow": ("https://api.toonflow.net/v1", "api.toonflow.net"),
        }
        for provider_id, (default_url, default_hosts) in provider_defaults.items():
            prefix = f"VIDEO_JOB_{provider_id.upper()}"
            base_url = os.getenv(f"{prefix}_BASE_URL", default_url).strip().rstrip("/")
            api_key = os.getenv(f"{prefix}_API_KEY", "").strip() if provider_id in enabled else ""
            parsed = urllib.parse.urlsplit(base_url)
            if provider_id in enabled and (parsed.scheme != "https" or not parsed.netloc):
                raise RuntimeError(f"{prefix}_BASE_URL must be an HTTPS URL")
            hosts = _host_list(os.getenv(f"{prefix}_RESULT_HOSTS", default_hosts), parsed.hostname or "")
            providers[provider_id] = ProviderConfig(
                id=provider_id,
                base_url=base_url,
                api_key=api_key,
                result_hosts=hosts,
                submit_timeout_seconds=_env_int(f"{prefix}_SUBMIT_TIMEOUT_SECONDS", 90, 15, 300),
                poll_timeout_seconds=_env_int(f"{prefix}_POLL_TIMEOUT_SECONDS", 60, 10, 180),
            )
        result_ttl = _env_int("VIDEO_JOB_GATEWAY_RESULT_TTL_SECONDS", 3 * 86400, 3600, 30 * 86400)
        metadata_ttl = _env_int("VIDEO_JOB_GATEWAY_METADATA_TTL_SECONDS", 30 * 86400, result_ttl, 90 * 86400)
        drain_file_name = (os.getenv("VIDEO_JOB_GATEWAY_DRAIN_FILE_NAME", "DRAIN").strip() or "DRAIN")[:80]
        if Path(drain_file_name).name != drain_file_name or drain_file_name in {".", ".."}:
            raise RuntimeError("VIDEO_JOB_GATEWAY_DRAIN_FILE_NAME must be a plain file name")
        return cls(
            token=token,
            data_dir=data_dir,
            catalog_file=catalog_file,
            providers=providers,
            pricing_url=os.getenv("VIDEO_JOB_GATEWAY_PRICING_URL", "http://new-api:3000/api/pricing").strip(),
            pricing_file=Path(
                os.getenv("VIDEO_JOB_GATEWAY_PRICING_FILE", str(Path(__file__).with_name("relay-pricing.json")))
            ).resolve(),
            pricing_group=os.getenv("VIDEO_JOB_GATEWAY_PRICING_GROUP", "视频").strip() or "视频",
            pricing_timeout_seconds=_env_int("VIDEO_JOB_GATEWAY_PRICING_TIMEOUT_SECONDS", 5, 1, 20),
            pricing_cache_seconds=_env_int("VIDEO_JOB_GATEWAY_PRICING_CACHE_SECONDS", 30, 5, 300),
            listen_host=os.getenv("VIDEO_JOB_GATEWAY_HOST", "0.0.0.0").strip() or "0.0.0.0",
            listen_port=_env_int("VIDEO_JOB_GATEWAY_PORT", 8091, 1, 65535),
            max_request_bytes=_env_int("VIDEO_JOB_GATEWAY_MAX_REQUEST_BYTES", 256 * 1024, 16 * 1024, 2 * 1024 * 1024),
            max_active_jobs=_env_int("VIDEO_JOB_GATEWAY_MAX_ACTIVE_JOBS", 500, 1, 5000),
            submit_concurrency=_env_int("VIDEO_JOB_GATEWAY_SUBMIT_CONCURRENCY", 20, 1, 100),
            poll_concurrency=_env_int("VIDEO_JOB_GATEWAY_POLL_CONCURRENCY", 50, 1, 200),
            stream_concurrency=_env_int("VIDEO_JOB_GATEWAY_STREAM_CONCURRENCY", 4, 1, 32),
            poll_interval_seconds=_env_int("VIDEO_JOB_GATEWAY_POLL_INTERVAL_SECONDS", 8, 5, 60),
            max_job_age_seconds=_env_int("VIDEO_JOB_GATEWAY_MAX_JOB_AGE_SECONDS", 8 * 3600, 15 * 60, 48 * 3600),
            uncertainty_window_seconds=_env_int("VIDEO_JOB_GATEWAY_UNCERTAINTY_WINDOW_SECONDS", 600, 60, 3600),
            uncertainty_count_threshold=_env_int("VIDEO_JOB_GATEWAY_UNCERTAINTY_COUNT_THRESHOLD", 2, 1, 100),
            uncertainty_rate_percent=_env_float("VIDEO_JOB_GATEWAY_UNCERTAINTY_RATE_PERCENT", 1.0, 0.1, 100.0),
            uncertainty_rate_min_samples=_env_int("VIDEO_JOB_GATEWAY_UNCERTAINTY_RATE_MIN_SAMPLES", 20, 2, 1000),
            drain_file_name=drain_file_name,
            result_ttl_seconds=result_ttl,
            metadata_ttl_seconds=metadata_ttl,
            max_stream_bytes=_env_int("VIDEO_JOB_GATEWAY_MAX_STREAM_BYTES", 512 * 1024 * 1024, 20 * 1024 * 1024, 2 * 1024 * 1024 * 1024),
        )


class GatewayError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str, *, category: str = "validation") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.category = category

    def contract(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": str(self)[:500],
            "http_status": int(self.status),
            "retryable": self.status in {HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE},
            "uncertain": False,
            "phase": "validate",
        }


class Gateway:
    def __init__(
        self,
        config: Config,
        *,
        catalog: Catalog | None = None,
        adapters: dict[str, VideoAdapter] | None = None,
        pricing: RelayPricing | None = None,
        start_monitor: bool = True,
    ) -> None:
        self.config = config
        self.catalog = catalog or Catalog.load(config.catalog_file)
        self.store = Store(config.data_dir, max_active_jobs=config.max_active_jobs)
        self.submit_slots = threading.BoundedSemaphore(config.submit_concurrency)
        self.poll_slots = threading.BoundedSemaphore(config.poll_concurrency)
        self.stream_slots = threading.BoundedSemaphore(config.stream_concurrency)
        self.stream_lock = threading.Lock()
        self.stream_active = 0
        self.stream_completed = 0
        self.stream_failed = 0
        self.stream_bytes = 0
        self.adapters = adapters or {
            "paisio": PaisioAdapter(config.providers["paisio"]),
            "rolldek": RollDekAdapter(config.providers["rolldek"]),
            "toonflow": ToonflowAdapter(config.providers["toonflow"]),
        }
        self.pricing = pricing or RelayPricing(
            config.pricing_file or Path(__file__).with_name("relay-pricing.json"),
            pricing_url=config.pricing_url,
            group_name=config.pricing_group,
            timeout_seconds=config.pricing_timeout_seconds,
            cache_seconds=config.pricing_cache_seconds,
        )
        self.stop_event = threading.Event()
        self.store.cleanup_expired(
            result_ttl_seconds=config.result_ttl_seconds,
            metadata_ttl_seconds=config.metadata_ttl_seconds,
        )
        for job_id in self.store.recover():
            self.start_submit(job_id)
        self.monitor_thread: threading.Thread | None = None
        if start_monitor:
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="video-job-monitor")
            self.monitor_thread.start()

    @property
    def drain_file(self) -> Path:
        return self.config.data_dir / self.config.drain_file_name

    @property
    def configured_providers(self) -> set[str]:
        ready = {
            provider_id
            for provider_id, adapter in self.adapters.items()
            if adapter.ready_for_new_jobs
        }
        store = getattr(self, "store", None)
        if store is None:
            return ready
        return ready - store.unhealthy_providers()

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
        providers = sorted(self.configured_providers)
        ready = bool(providers) and not draining and not circuit["open"]
        return ready, {
            "ok": ready,
            "service": "video-job-gateway",
            "submit_replay_safe": True,
            "idempotency_contract": DURABLE_REQUEST_ID_SUBMIT_CONTRACT,
            "protocol_version": self.catalog.protocol_version,
            "catalog_revision": self.catalog.revision,
            "accepting": ready,
            "draining": draining,
            "configured_provider_count": len(providers),
            "active_jobs": self.store.active_count(),
            "streaming": self.stream_snapshot(),
            "circuit": circuit,
            "time": int(time.time()),
        }

    def provider_health(self) -> dict[str, Any]:
        unhealthy = self.store.unhealthy_providers()
        rows = []
        for provider_id, adapter in sorted(self.adapters.items()):
            configured = bool(adapter.ready_for_new_jobs)
            rows.append(
                {
                    "provider_id": provider_id,
                    "configured": configured,
                    "eligible_for_new_jobs": configured and provider_id not in unhealthy,
                    "recent_definite_failure_threshold_reached": provider_id in unhealthy,
                }
            )
        return {
            "ok": True,
            "window_seconds": 300,
            "failure_threshold": 3,
            "providers": rows,
            "time": int(time.time()),
        }

    def capabilities(self) -> dict[str, Any]:
        snapshot = self.catalog.public_snapshot(self.configured_providers)
        ready, _ = self.readiness()
        capabilities = snapshot.get("capabilities") if isinstance(snapshot.get("capabilities"), dict) else {}
        video = capabilities.get("video") if isinstance(capabilities.get("video"), dict) else {}
        video["traffic_enabled"] = ready
        return snapshot

    def price_pairs(self) -> list[tuple[str, str]]:
        snapshot = self.catalog.public_snapshot(self.configured_providers)
        capabilities = snapshot.get("capabilities") if isinstance(snapshot.get("capabilities"), dict) else {}
        video = capabilities.get("video") if isinstance(capabilities.get("video"), dict) else {}
        pairs: list[tuple[str, str]] = []
        for row in video.get("models") or []:
            if not isinstance(row, dict):
                continue
            model = str(row.get("id") or "").strip()
            for raw_resolution in row.get("resolutions") or []:
                resolution = str(raw_resolution or "").strip().lower()
                if model and resolution:
                    pairs.append((model, resolution))
        return pairs

    def video_prices(self) -> dict[str, Any]:
        return {
            "ok": True,
            "protocol_version": self.catalog.protocol_version,
            "catalog_revision": self.catalog.revision,
            "pricing": self.pricing.snapshot(self.price_pairs()),
        }

    def stream_snapshot(self) -> dict[str, int]:
        with self.stream_lock:
            return {
                "active": self.stream_active,
                "concurrency_limit": self.config.stream_concurrency,
                "completed": self.stream_completed,
                "failed": self.stream_failed,
                "bytes": self.stream_bytes,
            }

    def stream_started(self) -> None:
        with self.stream_lock:
            self.stream_active += 1

    def stream_finished(self, transferred: int, succeeded: bool) -> None:
        with self.stream_lock:
            self.stream_active = max(0, self.stream_active - 1)
            self.stream_bytes += max(0, int(transferred or 0))
            if succeeded:
                self.stream_completed += 1
            else:
                self.stream_failed += 1

    def submit(self, raw: Any) -> tuple[dict[str, Any], bool]:
        request_id_hint = str(raw.get("request_id") or "").strip() if isinstance(raw, dict) else ""
        existing_internal = (
            self.store.get(request_id=request_id_hint, internal=True)
            if REQUEST_ID_PATTERN.fullmatch(request_id_hint)
            else None
        )
        replay_providers: set[str] | None = None
        if existing_internal:
            replay_providers = {
                provider_id
                for provider_id, adapter in self.adapters.items()
                if adapter.ready_for_new_jobs
            }
            replay_provider = str(existing_internal.get("provider_id") or "").strip().lower()
            if replay_provider:
                replay_providers.add(replay_provider)
        request_id, fingerprint, normalized, model, routes = self.validate_payload(
            raw,
            configured_providers=replay_providers,
        )
        route = routes[0]
        route_plan = [
            {
                "provider_id": candidate.provider,
                "upstream_model": candidate.upstream_model,
                "adapter_revision": candidate.adapter_revision,
                "send_resolution": candidate.send_resolution,
            }
            for candidate in routes
        ]
        payload_json = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        existing = self.store.get(request_id=request_id)
        if existing:
            try:
                return self.store.create(
                    request_id=request_id,
                    fingerprint=fingerprint,
                    protocol_version=self.catalog.protocol_version,
                    catalog_revision=self.catalog.revision,
                    stable_model=str(normalized.get("model") or model.id),
                    provider_id=route.provider,
                    upstream_model=route.upstream_model,
                    adapter_revision=route.adapter_revision,
                    payload_json=payload_json,
                    route_plan=route_plan,
                    selection_reason="deterministic_priority_rendezvous_v1",
                )
            except StoreConflict as error:
                raise GatewayError(HTTPStatus.CONFLICT, "request_id_conflict", str(error)) from error
        if self.drain_file.exists():
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_draining", "视频中转站正在排空，不接收新任务。", category="service_unavailable")
        if self.circuit_snapshot()["open"]:
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_circuit_open", "视频中转站因近期不确定任务暂停接收新任务。", category="service_unavailable")
        try:
            snapshot, reused = self.store.create(
                request_id=request_id,
                fingerprint=fingerprint,
                protocol_version=self.catalog.protocol_version,
                catalog_revision=self.catalog.revision,
                stable_model=str(normalized.get("model") or model.id),
                provider_id=route.provider,
                upstream_model=route.upstream_model,
                adapter_revision=route.adapter_revision,
                payload_json=payload_json,
                route_plan=route_plan,
                selection_reason="deterministic_priority_rendezvous_v1",
            )
        except StoreConflict as error:
            message = str(error)
            if "queue is full" in message:
                raise GatewayError(HTTPStatus.TOO_MANY_REQUESTS, "gateway_queue_full", message, category="service_unavailable") from error
            raise GatewayError(HTTPStatus.CONFLICT, "request_id_conflict", message) from error
        if not reused:
            self.start_submit(str(snapshot["job_id"]))
        return snapshot, reused

    def validate_payload(
        self,
        raw: Any,
        *,
        configured_providers: set[str] | None = None,
    ) -> tuple[str, str, dict[str, Any], Model, tuple[Route, ...]]:
        if not isinstance(raw, dict):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "payload_invalid", "请求体必须是JSON对象。")
        protocol_version = str(raw.get("protocol_version") or "").strip()
        if protocol_version != self.catalog.protocol_version:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "protocol_version_invalid", "中转协议版本无效。")
        if str(raw.get("capability") or "video.generate").strip() != "video.generate":
            raise GatewayError(HTTPStatus.BAD_REQUEST, "capability_invalid", "该接口只接受video.generate能力。")
        request_id = str(raw.get("request_id") or "").strip()
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "request_id_invalid", "request_id无效。")
        stable_model = str(raw.get("model") or "").strip()
        input_data = raw.get("input") if isinstance(raw.get("input"), dict) else {}
        parameters = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
        requested_resolution = str(parameters.get("resolution") or raw.get("resolution") or "").strip().lower()
        try:
            model, candidate_routes, resolution, legacy_alias = self.catalog.resolve_routes(
                stable_model,
                requested_resolution,
                self.configured_providers if configured_providers is None else configured_providers,
            )
            route = candidate_routes[0]
        except CatalogError as error:
            raise GatewayError(HTTPStatus.CONFLICT, "video_model_unavailable", str(error)) from error
        prompt = str(input_data.get("prompt") or raw.get("prompt") or "").strip()
        if not prompt or len(prompt) > 2500:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_prompt_invalid", "视频提示词不能为空且不能超过2500个字符。")
        mode = str(parameters.get("mode") or raw.get("mode") or "text").strip().lower()
        if mode not in ALLOWED_MODES or (model.operation_modes and mode not in model.operation_modes):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_mode_unsupported", "当前星途模型不支持所选视频模式。")
        try:
            duration = int(parameters.get("duration") or raw.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        if model.durations and duration not in model.durations:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_duration_unsupported", "当前星途模型不支持所选时长。")
        if not model.durations and not model.duration_min <= duration <= model.duration_max:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_duration_unsupported", "当前星途模型不支持所选时长。")
        aspect_ratio = str(parameters.get("aspect_ratio") or raw.get("aspect_ratio") or "16:9").strip()
        if model.aspect_ratios and aspect_ratio not in model.aspect_ratios:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_aspect_ratio_unsupported", "当前星途模型不支持所选画面比例。")
        candidate_routes = tuple(
            candidate
            for candidate in candidate_routes
            if not candidate.aspect_ratios or aspect_ratio in candidate.aspect_ratios
        )
        if not candidate_routes:
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "video_route_aspect_ratio_unsupported",
                "No configured video upstream supports the requested aspect ratio.",
            )
        route = candidate_routes[0]
        if route.aspect_ratios and aspect_ratio not in route.aspect_ratios:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_route_aspect_ratio_unsupported", "当前上游线路不支持所选画面比例。")
        images = _normalize_assets(input_data.get("images") or raw.get("images"), "image", ALLOWED_IMAGE_ROLES)
        videos = _normalize_assets(input_data.get("videos") or raw.get("videos"), "video", ALLOWED_VIDEO_ROLES)
        candidate_routes = tuple(
            candidate
            for candidate in candidate_routes
            if (not candidate.aspect_ratios or aspect_ratio in candidate.aspect_ratios)
            and (
                not candidate.max_total_assets
                or len(images) + len(videos) <= candidate.max_total_assets
            )
        )
        if not candidate_routes:
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "video_route_constraints_unsupported",
                "No configured video upstream supports the requested aspect ratio and references.",
            )
        route = candidate_routes[0]
        if len(images) > model.max_images or len(videos) > model.max_videos:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_reference_limit", "参考素材数量超过当前星途模型限制。")
        if route.max_total_assets and len(images) + len(videos) > route.max_total_assets:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_route_reference_limit", "参考素材总数超过当前上游线路限制。")
        if mode in {"first_frame", "reference"} and not images:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_reference_image_required", "当前视频模式至少需要一张参考图片。")
        if mode == "first_last_frame":
            roles = {item["role"] for item in images}
            if not {"first", "last"}.issubset(roles):
                raise GatewayError(HTTPStatus.BAD_REQUEST, "video_first_last_required", "首尾帧模式需要明确的首帧和尾帧图片。")
        if mode == "all_reference" and not images and not videos:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_reference_required", "全能参考模式至少需要一个参考素材。")
        normalized = {
            "model": stable_model if legacy_alias else model.id,
            "prompt": prompt,
            "mode": mode,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "images": images,
            "videos": videos,
            "generate_audio": bool(parameters.get("generate_audio")) if "generate_audio" in parameters else None,
            "negative_prompt": str(parameters.get("negative_prompt") or "").strip()[:2000],
            "delivery": {"prefer_direct_url": True},
        }
        if resolution and (not legacy_alias or requested_resolution):
            normalized["resolution"] = resolution
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        normalized["_route"] = {
            "resolution": resolution,
            "send_resolution": route.send_resolution,
        }
        try:
            normalized["_relay_price"] = self.pricing.quote(model.id, resolution, duration)
        except RelayPricingError as error:
            raise GatewayError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "relay_video_price_unavailable",
                "星途中转站当前无法取得安全的视频价格。",
                category="service_unavailable",
            ) from error
        try:
            routes = build_route_plan(
                request_id=request_id,
                stable_model=model.id,
                resolution=resolution,
                routes=candidate_routes,
            )
        except RoutePlanError as error:
            raise GatewayError(HTTPStatus.CONFLICT, "video_model_unavailable", str(error)) from error
        return request_id, fingerprint, normalized, model, routes

    def start_submit(self, job_id: str) -> None:
        threading.Thread(target=self._submit_one, args=(job_id,), daemon=True, name=f"video-submit-{job_id[-8:]}").start()

    def _submit_one(self, job_id: str) -> None:
        with self.submit_slots:
            self._submit_with_fallback(job_id)

    def _submit_with_fallback(self, job_id: str) -> None:
        while True:
            job = self.store.claim_submit(job_id)
            if not job:
                return
            adapter = self.adapters.get(str(job.get("provider_id") or ""))
            if not adapter or not adapter.config.configured:
                error = _error(
                    "video_provider_not_configured",
                    "service_unavailable",
                    "Video upstream is not configured.",
                    503,
                    False,
                    False,
                    "validate",
                )
                if self.store.advance_route(job_id, error=error):
                    continue
                self.store.finish(job_id, "failed", error=error)
                return
            try:
                payload = json.loads(str(job.get("payload_json") or "{}"))
                route_plan = json.loads(str(job.get("route_plan_json") or "[]"))
                route_index = int(job.get("route_index") or 0)
                route_settings = (
                    route_plan[route_index]
                    if isinstance(route_plan, list) and 0 <= route_index < len(route_plan)
                    else {}
                )
                if "send_resolution" in route_settings:
                    payload.setdefault("_route", {})["send_resolution"] = bool(
                        route_settings.get("send_resolution", False)
                    )
                observation = adapter.submit(
                    str(job["request_id"]),
                    str(job["upstream_model"]),
                    payload,
                )
                if observation.status == "failed":
                    error = _error(
                        observation.error_code or "upstream_video_failed",
                        "upstream",
                        observation.error_message or "Upstream video generation was rejected.",
                        502,
                        observation.retryable,
                        False,
                        "submit",
                    )
                    if not observation.upstream_task_id and self.store.advance_route(job_id, error=error):
                        continue
                    self.store.finish(
                        job_id,
                        "failed",
                        error=error,
                        upstream_task_id=observation.upstream_task_id,
                        upstream_status=observation.upstream_status,
                    )
                elif observation.status == "succeeded" and observation.result_url:
                    self.store.finish(
                        job_id,
                        "succeeded",
                        result=self._result(job, observation.result_url, observation.requires_auth),
                        upstream_task_id=observation.upstream_task_id,
                        upstream_status=observation.upstream_status,
                    )
                else:
                    self.store.mark_running(
                        job_id,
                        observation.upstream_task_id,
                        observation.upstream_status or observation.status,
                        self.config.poll_interval_seconds,
                    )
                return
            except AdapterError as error:
                contract = error.contract()
                if not error.uncertain and self.store.advance_route(job_id, error=contract):
                    continue
                self.store.finish(
                    job_id,
                    "uncertain" if error.uncertain else "failed",
                    error=contract,
                )
                return
            except Exception as error:
                self.store.finish(
                    job_id,
                    "uncertain",
                    error=_error(
                        "gateway_execution_uncertain",
                        "internal",
                        f"Gateway execution failed with {type(error).__name__}; submit result is unknown.",
                        503,
                        False,
                        True,
                        "submit",
                    ),
                )
                return

    def _monitor_loop(self) -> None:
        last_cleanup = 0
        while not self.stop_event.wait(1.0):
            current = int(time.time())
            if current - last_cleanup >= 300:
                self.store.cleanup_expired(
                    result_ttl_seconds=self.config.result_ttl_seconds,
                    metadata_ttl_seconds=self.config.metadata_ttl_seconds,
                )
                last_cleanup = current
            for job in self.store.due_poll_jobs(limit=self.config.poll_concurrency, lease_seconds=30):
                threading.Thread(target=self._poll_one, args=(job,), daemon=True, name=f"video-poll-{str(job['job_id'])[-8:]}").start()

    def _poll_one(self, job: dict[str, Any]) -> None:
        with self.poll_slots:
            job_id = str(job["job_id"])
            if int(time.time()) - int(job.get("created_at") or 0) > self.config.max_job_age_seconds:
                self.store.finish(
                    job_id,
                    "pending_review",
                    error=_error("video_job_age_exceeded", "upstream", "视频任务超过自动查询时限，已转人工核对且不会重提。", 504, False, True, "poll"),
                    upstream_task_id=str(job.get("upstream_task_id") or ""),
                    upstream_status=str(job.get("upstream_status") or ""),
                )
                return
            adapter = self.adapters.get(str(job.get("provider_id") or ""))
            if not adapter or not adapter.config.configured:
                self._delay_poll(job_id, str(job.get("upstream_status") or ""), _error("video_provider_not_configured", "service_unavailable", "视频上游查询凭据暂不可用。", 503, True, True, "poll"))
                return
            try:
                observation = adapter.poll(str(job.get("upstream_task_id") or ""))
            except AdapterError as error:
                self._delay_poll(job_id, str(job.get("upstream_status") or ""), error.contract())
                return
            if observation.status == "missing":
                count, _ = self.store.continue_running(
                    job_id,
                    upstream_status="not_found",
                    poll_delay=max(10, self.config.poll_interval_seconds),
                    missing=True,
                )
                if count >= 2:
                    self.store.finish(
                        job_id,
                        "failed",
                        error=_error("upstream_job_not_found_confirmed", "upstream", "上游连续两次确认任务不存在。", 404, False, False, "poll"),
                        upstream_task_id=str(job.get("upstream_task_id") or ""),
                        upstream_status="not_found",
                    )
                return
            if observation.status == "succeeded":
                self.store.finish(
                    job_id,
                    "succeeded",
                    result=self._result(job, observation.result_url, observation.requires_auth),
                    upstream_task_id=observation.upstream_task_id,
                    upstream_status=observation.upstream_status,
                )
            elif observation.status == "failed":
                self.store.finish(
                    job_id,
                    "failed",
                    error=_error(
                        observation.error_code or "upstream_video_failed",
                        "upstream",
                        observation.error_message or "上游视频任务失败。",
                        502,
                        observation.retryable,
                        False,
                        "poll",
                    ),
                    upstream_task_id=observation.upstream_task_id,
                    upstream_status=observation.upstream_status,
                )
            else:
                self.store.continue_running(
                    job_id,
                    upstream_status=observation.upstream_status or observation.status,
                    poll_delay=self.config.poll_interval_seconds,
                )

    def _delay_poll(self, job_id: str, upstream_status: str, error: dict[str, Any]) -> None:
        current = self.store.get(job_id=job_id, internal=True) or {}
        failures = max(0, int(current.get("poll_errors") or 0))
        delay = min(60, self.config.poll_interval_seconds * (2 ** min(failures, 3)))
        self.store.continue_running(
            job_id,
            upstream_status=upstream_status,
            poll_delay=delay,
            error=error,
        )

    def _result(
        self,
        job: Mapping[str, Any],
        source_url: str,
        requires_auth: bool,
    ) -> dict[str, Any]:
        result = {
            "type": "url",
            "delivery_mode": "direct_url",
            "source_url": source_url,
            "content_type": "video/mp4",
            "requires_auth": bool(requires_auth),
        }
        try:
            payload = json.loads(str(job.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        relay_price = payload.get("_relay_price") if isinstance(payload, dict) and isinstance(payload.get("_relay_price"), dict) else {}
        if relay_price:
            try:
                amount = Decimal(str(relay_price.get("amount_cny_exact") or ""))
                observed_at = int(time.time())
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ValueError("relay job contains invalid frozen price evidence") from error
            if (
                relay_price.get("contract_version") != PRICE_CONTRACT_VERSION
                or not amount.is_finite()
                or amount <= 0
                or amount > Decimal("100000")
            ):
                raise ValueError("relay job contains invalid frozen price evidence")
            amount_exact = format(amount, "f")
            issuer = "xtai-video-relay"
            gateway_job_id = str(job.get("job_id") or "")
            gateway_request_id = str(job.get("request_id") or "")
            material = "\0".join((
                "xtai-video-actual-cost-v1",
                issuer,
                gateway_job_id,
                gateway_request_id,
                amount_exact,
                "CNY",
                "request",
                "final",
                str(observed_at),
            ))
            result["billing"] = {
                "contract_version": "xtai-video-actual-cost-v1",
                "issuer": issuer,
                "gateway_job_id": gateway_job_id,
                "gateway_request_id": gateway_request_id,
                "currency": "CNY",
                "amount_cny_exact": amount_exact,
                "billing_unit": "request",
                "status": "final",
                "observed_at": observed_at,
                "pricing_contract_version": PRICE_CONTRACT_VERSION,
                "pricing_revision": str(relay_price.get("pricing_revision") or "")[:160],
                "price_source": str(relay_price.get("price_source") or "")[:80],
                "evidence_hash": hmac.new(
                    self.config.token.encode("utf-8"),
                    material.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
            }
        return result

    def result_source(self, job_id: str) -> tuple[str, ProviderConfig] | None:
        job = self.store.get(job_id=job_id, internal=True)
        if not job or job.get("status") != "succeeded" or not job.get("result_json"):
            return None
        try:
            result = json.loads(str(job["result_json"]))
        except json.JSONDecodeError:
            return None
        url = str(result.get("source_url") or "").strip() if isinstance(result, dict) else ""
        provider = self.config.providers.get(str(job.get("provider_id") or ""))
        return (url, provider) if url and provider else None


def handler_class(gateway: Gateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "XingTuVideoJobGateway/1"

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
            if self.command != "HEAD":
                self.wfile.write(body)

        def authorized(self) -> bool:
            expected = f"Bearer {gateway.config.token}"
            supplied = self.headers.get("Authorization", "")
            return bool(supplied) and hmac.compare_digest(supplied, expected)

        def serve_get_or_head(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/health":
                self.json_response(HTTPStatus.OK, {
                    "ok": True,
                    "service": "video-job-gateway",
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
                self.json_response(HTTPStatus.UNAUTHORIZED, {"error": _error("unauthorized", "authentication", "未授权。", 401, False, False, "validate")})
                return
            if parsed.path == "/v1/capabilities":
                self.json_response(HTTPStatus.OK, {"ok": True, **gateway.capabilities()})
                return
            if parsed.path == "/v1/video-prices":
                self.json_response(HTTPStatus.OK, gateway.video_prices())
                return
            if parsed.path == "/v1/operations/provider-health":
                self.json_response(HTTPStatus.OK, gateway.provider_health())
                return
            content_match = re.fullmatch(r"/v1/video-jobs/(vjob_[0-9a-f]{32})/content", parsed.path)
            if content_match:
                try:
                    _stream_result(self, gateway, content_match.group(1), head_only=self.command == "HEAD")
                except GatewayError as error:
                    self.json_response(error.status, {"error": error.contract()})
                return
            snapshot = None
            if parsed.path.startswith("/v1/video-jobs/by-request/"):
                request_id = urllib.parse.unquote(parsed.path.removeprefix("/v1/video-jobs/by-request/"))
                if REQUEST_ID_PATTERN.fullmatch(request_id):
                    snapshot = gateway.store.get(request_id=request_id)
            elif parsed.path.startswith("/v1/video-jobs/"):
                job_id = parsed.path.removeprefix("/v1/video-jobs/")
                if JOB_ID_PATTERN.fullmatch(job_id):
                    snapshot = gateway.store.get(job_id=job_id)
            if snapshot is None:
                self.json_response(HTTPStatus.NOT_FOUND, {"error": _error("job_not_found", "validation", "视频任务不存在。", 404, False, False, "poll")})
                return
            self.json_response(HTTPStatus.OK, {"ok": True, "job": snapshot})

        def do_GET(self) -> None:
            self.serve_get_or_head()

        def do_HEAD(self) -> None:
            self.serve_get_or_head()

        def do_POST(self) -> None:
            if urllib.parse.urlsplit(self.path).path != "/v1/video-jobs":
                self.json_response(HTTPStatus.NOT_FOUND, {"error": _error("not_found", "validation", "接口不存在。", 404, False, False, "validate")})
                return
            if not self.authorized():
                self.json_response(HTTPStatus.UNAUTHORIZED, {"error": _error("unauthorized", "authentication", "未授权。", 401, False, False, "validate")})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > gateway.config.max_request_bytes:
                self.json_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": _error("request_too_large", "validation", "请求体大小无效。", 413, False, False, "validate")})
                return
            try:
                raw = json.loads(self.rfile.read(length).decode("utf-8"))
                snapshot, reused = gateway.submit(raw)
                self.json_response(HTTPStatus.ACCEPTED, {"ok": True, "reused": reused, "job": snapshot})
            except GatewayError as error:
                self.json_response(error.status, {"error": error.contract()})
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.json_response(HTTPStatus.BAD_REQUEST, {"error": _error("json_invalid", "validation", "请求体不是有效JSON。", 400, False, False, "validate")})

    return Handler


def _stream_result(handler: BaseHTTPRequestHandler, gateway: Gateway, job_id: str, *, head_only: bool) -> None:
    if not gateway.stream_slots.acquire(blocking=False):
        raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "result_stream_busy", "视频中转流式并发已满。", category="service_unavailable")
    gateway.stream_started()
    transferred = 0
    succeeded = False
    try:
        source = gateway.result_source(job_id)
        if not source:
            raise GatewayError(HTTPStatus.NOT_FOUND, "result_not_found", "视频结果不存在或已过期。")
        url, provider = source
        range_header = str(handler.headers.get("Range") or "").strip()
        response = _open_safe_result(
            url,
            provider,
            method="HEAD" if head_only else "GET",
            range_header=range_header,
            timeout=60,
        )
        try:
            try:
                length = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length > gateway.config.max_stream_bytes:
                raise GatewayError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "result_too_large", "视频结果超过中转流式上限。")
            status = HTTPStatus.PARTIAL_CONTENT if int(getattr(response, "status", 200)) == 206 else HTTPStatus.OK
            handler.send_response(status)
            handler.send_header("Content-Type", str(response.headers.get("Content-Type") or "video/mp4"))
            if length:
                handler.send_header("Content-Length", str(length))
            for name in ("Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
                value = response.headers.get(name)
                if value:
                    handler.send_header(name, str(value))
            handler.send_header("Cache-Control", "private, no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()
            if head_only:
                succeeded = True
                return
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    succeeded = True
                    break
                transferred += len(chunk)
                if transferred > gateway.config.max_stream_bytes:
                    handler.close_connection = True
                    break
                handler.wfile.write(chunk)
        finally:
            response.close()
    finally:
        gateway.stream_finished(transferred, succeeded)
        gateway.stream_slots.release()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _open_safe_result(
    url: str,
    provider: ProviderConfig,
    *,
    method: str,
    range_header: str,
    timeout: int,
) -> Any:
    opener = urllib.request.build_opener(_NoRedirect())
    current = url
    base_host = (urllib.parse.urlsplit(provider.base_url).hostname or "").lower()
    for _ in range(4):
        host = _validate_result_url(current, provider.result_hosts)
        headers = {"Accept": "video/mp4,application/octet-stream", "User-Agent": "XingTuVideoJobGateway/1"}
        if range_header:
            headers["Range"] = range_header
        if host == base_host:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        request = urllib.request.Request(current, headers=headers, method=method)
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_fetch_failed", "上游视频结果暂时无法读取。", category="upstream") from error
            location = str(error.headers.get("Location") or "").strip()
            error.close()
            if not location:
                raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_redirect_invalid", "上游视频结果重定向无效。", category="upstream")
            current = urllib.parse.urljoin(current, location)
        except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout, ssl.SSLError) as error:
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_fetch_unavailable", "上游视频结果暂时无法读取。", category="upstream") from error
    raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_redirect_limit", "上游视频结果重定向次数过多。", category="upstream")


def _validate_result_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment:
        raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_url_invalid", "上游视频结果地址不安全。", category="upstream")
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
        raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_host_not_allowed", "上游视频结果主机不在允许列表中。", category="upstream")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_host_unresolved", "上游视频结果主机暂时无法解析。", category="upstream") from error
    for value in addresses:
        address = ipaddress.ip_address(value.split("%")[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "result_host_private", "上游视频结果主机解析到不安全地址。", category="upstream")
    return host


def _normalize_assets(raw: Any, kind: str, allowed_roles: set[str]) -> list[dict[str, str]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise GatewayError(HTTPStatus.BAD_REQUEST, f"video_{kind}_assets_invalid", "参考素材必须是数组。")
    result: list[dict[str, str]] = []
    for item in raw:
        source = item if isinstance(item, dict) else {"url": item}
        url = str(source.get("url") or "").strip()
        role = str(source.get("role") or "reference").strip().lower()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or len(url) > 4096:
            raise GatewayError(HTTPStatus.BAD_REQUEST, f"video_{kind}_url_invalid", "参考素材必须使用安全的HTTPS地址。")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal and (literal.is_private or literal.is_loopback or literal.is_link_local or literal.is_reserved):
            raise GatewayError(HTTPStatus.BAD_REQUEST, f"video_{kind}_url_invalid", "参考素材地址不能指向私有网络。")
        if role not in allowed_roles:
            raise GatewayError(HTTPStatus.BAD_REQUEST, f"video_{kind}_role_invalid", "参考素材角色无效。")
        result.append({"url": url, "role": role})
    return result


def _error(
    code: str,
    category: str,
    message: str,
    http_status: int,
    retryable: bool,
    uncertain: bool,
    phase: str,
) -> dict[str, Any]:
    return {
        "code": str(code or "video_gateway_error")[:80],
        "category": category,
        "message": str(message or "视频中转任务失败。")[:500],
        "http_status": int(http_status or 500),
        "retryable": bool(retryable),
        "uncertain": bool(uncertain),
        "phase": phase,
    }


def _host_list(raw: str, base_host: str) -> tuple[str, ...]:
    values: list[str] = []
    for value in [*str(raw or "").split(","), base_host]:
        host = str(value or "").strip().lower().rstrip(".")
        if host and re.fullmatch(r"[a-z0-9.-]+", host) and host not in values:
            values.append(host)
    return tuple(values)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def main() -> None:
    config = Config.from_env()
    gateway = Gateway(config)
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), handler_class(gateway))
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
