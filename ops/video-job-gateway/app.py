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
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from adapters import AdapterError, PaisioAdapter, ProviderConfig, RollDekAdapter, ToonflowAdapter, VideoAdapter
from billing_collectors import (
    BillingCollectionError,
    NewAPITaskBillingCollector,
    ToonflowBillingCollector,
)
from catalog import Catalog, CatalogError, Model, Route
from relay_pricing import PRICE_CONTRACT_VERSION, RelayPricing, RelayPricingError
from reference_contract import ReferenceContractError, ReferenceMediaVerifier, stable_reference_identity, validate_reference_payload
from routing import RoutePlanError, build_route_plan
from store import (
    BILLING_CONTRACT_REFERENCE_VERSION,
    BILLING_CONTRACT_VERSION,
    BILLING_CONTRACT_VERSIONS,
    GENERATION_QUARANTINE_FAILURE_THRESHOLD,
    GENERATION_QUARANTINE_WINDOW_SECONDS,
    Store,
    StoreConflict,
    build_settlement_evidence,
)


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
JOB_ID_PATTERN = re.compile(r"^vjob_[0-9a-f]{32}$")
DURABLE_REQUEST_ID_SUBMIT_CONTRACT = "durable-request-id-submit-v1"
ALLOWED_MODES = {"text", "first_frame", "first_last_frame", "reference", "all_reference"}
ALLOWED_IMAGE_ROLES = {"reference", "first", "last", "style"}
ALLOWED_VIDEO_ROLES = {"reference", "camera_motion"}
ALLOWED_AUDIO_ROLES = {"reference_audio"}
VIDEO_BILLING_V2_CONTRACT = BILLING_CONTRACT_VERSION
VIDEO_REFERENCE_V22_CONTRACT = BILLING_CONTRACT_REFERENCE_VERSION


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
    public_base_url: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: int = 10
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
    toonflow_billing_enabled: bool = False
    toonflow_billing_log_url: str = "https://api.toonflow.net/web/web/operationLog/getOperationLog"
    toonflow_billing_token: str = field(default="", repr=False)
    toonflow_billing_token_file: Path | None = field(default=None, repr=False)
    toonflow_billing_timeout_seconds: int = 10
    newapi_billing_enabled_providers: frozenset[str] = field(default_factory=frozenset)
    newapi_billing_credential_files: dict[str, Path] = field(default_factory=dict, repr=False)
    newapi_billing_rates_cny_per_usd: dict[str, str] = field(default_factory=dict)
    paisio_identity_resolver_enabled: bool = False
    v21_approved_providers: frozenset[str] = field(
        default_factory=lambda: frozenset({"toonflow"})
    )
    v22_reference_video_enabled: bool = False
    v22_reference_audio_enabled: bool = False
    v22_reference_combined_enabled: bool = False
    reference_media_hosts: tuple[str, ...] = ()
    reference_media_timeout_seconds: int = 60
    reference_verify_concurrency: int = 2
    settlement_query_concurrency: int = 2
    settlement_query_interval_seconds: int = 60
    settlement_provider_quarantine_age_seconds: int = 30 * 60
    settlement_provider_quarantine_attempts: int = 3

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
            "toonflow": (
                "https://api.toonflow.net/v1",
                "api.toonflow.net,tos-cn-beijing.volces.com",
            ),
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
        webhook_enabled = _env_bool("VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED", False)
        webhook_url = _validated_webhook_url(os.getenv("VIDEO_JOB_GATEWAY_WEBHOOK_URL", ""))
        webhook_secret = os.getenv("VIDEO_JOB_GATEWAY_WEBHOOK_SECRET", "").strip()
        if webhook_enabled and (not webhook_url or len(webhook_secret.encode("utf-8")) < 32):
            raise RuntimeError("enabled video webhook requires a safe HTTPS URL and at least 32 secret bytes")
        toonflow_billing_enabled = _env_bool("VIDEO_JOB_TOONFLOW_BILLING_ENABLED", False)
        toonflow_billing_log_url = _validated_provider_billing_url(
            os.getenv(
                "VIDEO_JOB_TOONFLOW_BILLING_LOG_URL",
                "https://api.toonflow.net/web/web/operationLog/getOperationLog",
            ),
            providers["toonflow"].base_url,
        )
        toonflow_billing_token = os.getenv("VIDEO_JOB_TOONFLOW_BILLING_TOKEN", "").strip()
        toonflow_billing_token_file_raw = os.getenv("VIDEO_JOB_TOONFLOW_BILLING_TOKEN_FILE", "").strip()
        toonflow_billing_token_file = (
            Path(os.path.abspath(toonflow_billing_token_file_raw))
            if toonflow_billing_token_file_raw
            else None
        )
        if (
            toonflow_billing_enabled
            and len(toonflow_billing_token.encode("utf-8")) < 20
            and toonflow_billing_token_file is None
        ):
            raise RuntimeError("enabled Toonflow billing collection requires a separate service token")
        newapi_billing_enabled = frozenset(
            provider_id
            for provider_id in ("paisio", "rolldek")
            if _env_bool(f"VIDEO_JOB_{provider_id.upper()}_BILLING_ENABLED", False)
        )
        v21_approved_providers = frozenset(
            value.strip().lower()
            for value in os.getenv("VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS", "toonflow").split(",")
            if value.strip().lower() in {"paisio", "rolldek", "toonflow"}
        )
        newapi_billing_files: dict[str, Path] = {}
        newapi_billing_rates: dict[str, str] = {}
        for provider_id in newapi_billing_enabled:
            prefix = f"VIDEO_JOB_{provider_id.upper()}"
            billing_origin = urllib.parse.urlsplit(providers[provider_id].base_url)
            if billing_origin.scheme != "https" or not billing_origin.hostname:
                raise RuntimeError(f"{prefix}_BASE_URL must be an HTTPS URL for billing collection")
            raw_path = os.getenv(f"{prefix}_BILLING_CREDENTIAL_FILE", "").strip()
            if not raw_path:
                raise RuntimeError(f"enabled {provider_id} billing collection requires a credential file")
            newapi_billing_files[provider_id] = Path(os.path.abspath(raw_path))
            raw_rate = os.getenv(f"{prefix}_BILLING_RATE_CNY_PER_USD", "1").strip()
            try:
                rate = Decimal(raw_rate)
            except InvalidOperation as error:
                raise RuntimeError(f"{prefix}_BILLING_RATE_CNY_PER_USD must be positive") from error
            if not rate.is_finite() or rate <= 0:
                raise RuntimeError(f"{prefix}_BILLING_RATE_CNY_PER_USD must be positive")
            newapi_billing_rates[provider_id] = format(rate, "f")
        reference_media_hosts = _host_list(
            os.getenv("VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_HOSTS", ""), ""
        )
        if any(
            _env_bool(name, False)
            for name in (
                "VIDEO_JOB_GATEWAY_V22_REFERENCE_VIDEO_ENABLED",
                "VIDEO_JOB_GATEWAY_V22_REFERENCE_AUDIO_ENABLED",
                "VIDEO_JOB_GATEWAY_V22_REFERENCE_COMBINED_ENABLED",
            )
        ) and not reference_media_hosts:
            raise RuntimeError("enabled v2.2 reference contracts require an explicit media host allowlist")
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
            public_base_url=_validated_public_base_url(os.getenv("VIDEO_JOB_GATEWAY_PUBLIC_BASE_URL", "")),
            webhook_enabled=webhook_enabled,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            webhook_timeout_seconds=_env_int("VIDEO_JOB_GATEWAY_WEBHOOK_TIMEOUT_SECONDS", 10, 3, 30),
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
            toonflow_billing_enabled=toonflow_billing_enabled,
            toonflow_billing_log_url=toonflow_billing_log_url,
            toonflow_billing_token=toonflow_billing_token,
            toonflow_billing_token_file=toonflow_billing_token_file,
            toonflow_billing_timeout_seconds=_env_int("VIDEO_JOB_TOONFLOW_BILLING_TIMEOUT_SECONDS", 10, 3, 30),
            newapi_billing_enabled_providers=newapi_billing_enabled,
            newapi_billing_credential_files=newapi_billing_files,
            newapi_billing_rates_cny_per_usd=newapi_billing_rates,
            paisio_identity_resolver_enabled=_env_bool(
                "VIDEO_JOB_PAISIO_IDENTITY_RESOLVER_ENABLED", False
            ),
            v21_approved_providers=v21_approved_providers,
            v22_reference_video_enabled=_env_bool("VIDEO_JOB_GATEWAY_V22_REFERENCE_VIDEO_ENABLED", False),
            v22_reference_audio_enabled=_env_bool("VIDEO_JOB_GATEWAY_V22_REFERENCE_AUDIO_ENABLED", False),
            v22_reference_combined_enabled=_env_bool("VIDEO_JOB_GATEWAY_V22_REFERENCE_COMBINED_ENABLED", False),
            reference_media_hosts=reference_media_hosts,
            reference_media_timeout_seconds=_env_int(
                "VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_TIMEOUT_SECONDS", 60, 10, 300
            ),
            reference_verify_concurrency=_env_int(
                "VIDEO_JOB_GATEWAY_REFERENCE_VERIFY_CONCURRENCY", 2, 1, 10
            ),
            settlement_query_concurrency=_env_int("VIDEO_JOB_GATEWAY_SETTLEMENT_QUERY_CONCURRENCY", 2, 1, 10),
            settlement_query_interval_seconds=_env_int("VIDEO_JOB_GATEWAY_SETTLEMENT_QUERY_INTERVAL_SECONDS", 60, 15, 3600),
            settlement_provider_quarantine_age_seconds=_env_int(
                "VIDEO_JOB_GATEWAY_SETTLEMENT_PROVIDER_QUARANTINE_AGE_SECONDS",
                30 * 60,
                5 * 60,
                24 * 60 * 60,
            ),
            settlement_provider_quarantine_attempts=_env_int(
                "VIDEO_JOB_GATEWAY_SETTLEMENT_PROVIDER_QUARANTINE_ATTEMPTS",
                3,
                1,
                100,
            ),
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
        billing_collectors: dict[str, Any] | None = None,
        pricing: RelayPricing | None = None,
        reference_verifier: Any | None = None,
        start_monitor: bool = True,
    ) -> None:
        self.config = config
        self.catalog = catalog or Catalog.load(config.catalog_file)
        self.store = Store(
            config.data_dir,
            max_active_jobs=config.max_active_jobs,
            public_base_url=config.public_base_url,
        )
        self.submit_slots = threading.BoundedSemaphore(config.submit_concurrency)
        self.poll_slots = threading.BoundedSemaphore(config.poll_concurrency)
        self.stream_slots = threading.BoundedSemaphore(config.stream_concurrency)
        self.settlement_slots = threading.BoundedSemaphore(config.settlement_query_concurrency)
        self.reference_verify_slots = threading.BoundedSemaphore(config.reference_verify_concurrency)
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
        if billing_collectors is not None:
            self.billing_collectors = dict(billing_collectors)
        else:
            self.billing_collectors = {}
            if config.toonflow_billing_enabled:
                self.billing_collectors["toonflow"] = ToonflowBillingCollector(
                    config.toonflow_billing_log_url,
                    config.toonflow_billing_token,
                    token_file=config.toonflow_billing_token_file,
                    timeout_seconds=config.toonflow_billing_timeout_seconds,
                )
            for provider_id in sorted(config.newapi_billing_enabled_providers):
                provider = config.providers[provider_id]
                self.billing_collectors[provider_id] = NewAPITaskBillingCollector(
                    provider_id,
                    f"{provider.base_url}/api/task/self",
                    credential_file=config.newapi_billing_credential_files[provider_id],
                    rate_cny_per_usd=config.newapi_billing_rates_cny_per_usd.get(provider_id, "1"),
                    timeout_seconds=provider.poll_timeout_seconds,
                    result_hosts=provider.result_hosts,
                    max_media_bytes=config.max_stream_bytes,
                    identity_resolution_enabled=(
                        config.paisio_identity_resolver_enabled
                        if provider_id == "paisio"
                        else True
                    ),
                )
        self.pricing = pricing or RelayPricing(
            config.pricing_file or Path(__file__).with_name("relay-pricing.json"),
            pricing_url=config.pricing_url,
            group_name=config.pricing_group,
            timeout_seconds=config.pricing_timeout_seconds,
            cache_seconds=config.pricing_cache_seconds,
        )
        self.reference_verifier = reference_verifier or ReferenceMediaVerifier(
            config.reference_media_hosts,
            timeout_seconds=config.reference_media_timeout_seconds,
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
        """Providers with generation credentials that may serve legacy/replay work."""
        ready = {
            provider_id
            for provider_id, adapter in self.adapters.items()
            if adapter.ready_for_new_jobs
        }
        store = getattr(self, "store", None)
        if store is None:
            return ready
        return ready - store.unhealthy_providers()

    @property
    def eligible_v2_providers(self) -> set[str]:
        """Providers safe for a new actual-cost-settled v2.1 task."""
        billing_ready = {
            provider_id
            for provider_id, collector in self.billing_collectors.items()
            if bool(getattr(collector, "identity_ready", getattr(collector, "ready", False)))
        }
        approved = set(
            getattr(
                getattr(self, "config", None),
                "v21_approved_providers",
                frozenset(billing_ready),
            )
        )
        settlement_unhealthy = self.store.unhealthy_settlement_providers(
            min_age_seconds=getattr(
                self.config,
                "settlement_provider_quarantine_age_seconds",
                30 * 60,
            ),
            min_attempts=getattr(
                self.config,
                "settlement_provider_quarantine_attempts",
                3,
            ),
        )
        return (self.configured_providers & billing_ready & approved) - settlement_unhealthy

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
        providers = sorted(self.eligible_v2_providers)
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
        generation_quarantines = set(self.store.generation_quarantines())
        settlement_unhealthy = self.store.unhealthy_settlement_providers(
            min_age_seconds=getattr(
                self.config,
                "settlement_provider_quarantine_age_seconds",
                30 * 60,
            ),
            min_attempts=getattr(
                self.config,
                "settlement_provider_quarantine_attempts",
                3,
            ),
        )
        rows = []
        for provider_id, adapter in sorted(self.adapters.items()):
            generation_ready = bool(adapter.ready_for_new_jobs)
            collector = self.billing_collectors.get(provider_id)
            billing_ready = bool(
                collector is not None
                and getattr(collector, "identity_ready", getattr(collector, "ready", False))
            )
            approved = provider_id in set(
                getattr(
                    getattr(self, "config", None),
                    "v21_approved_providers",
                    frozenset(self.billing_collectors),
                )
            )
            eligible = (
                generation_ready
                and billing_ready
                and approved
                and provider_id not in unhealthy
                and provider_id not in generation_quarantines
                and provider_id not in settlement_unhealthy
            )
            if not generation_ready:
                exclusion_reason = "generation_not_ready"
            elif not billing_ready:
                exclusion_reason = "billing_not_ready"
            elif not approved:
                exclusion_reason = "billing_not_approved"
            elif provider_id in generation_quarantines:
                exclusion_reason = "provider_generation_quarantined"
            elif provider_id in settlement_unhealthy:
                exclusion_reason = "billing_settlement_backlog"
            elif provider_id in unhealthy:
                exclusion_reason = "provider_temporarily_unhealthy"
            else:
                exclusion_reason = ""
            rows.append(
                {
                    "provider_id": provider_id,
                    "configured": generation_ready,
                    "generation_ready": generation_ready,
                    "billing_ready": billing_ready,
                    "billing_approved": approved,
                    "eligible_for_new_jobs": eligible,
                    "eligible_for_new_v21_jobs": eligible,
                    "exclusion_reason": exclusion_reason,
                    "recent_definite_failure_threshold_reached": (
                        provider_id in unhealthy and provider_id not in generation_quarantines
                    ),
                    "persistent_generation_quarantine_active": provider_id in generation_quarantines,
                    "settlement_backlog_threshold_reached": provider_id in settlement_unhealthy,
                }
            )
        return {
            "ok": True,
            "window_seconds": 300,
            "failure_threshold": 3,
            "persistent_generation_quarantine_window_seconds": GENERATION_QUARANTINE_WINDOW_SECONDS,
            "persistent_generation_quarantine_failure_threshold": GENERATION_QUARANTINE_FAILURE_THRESHOLD,
            "providers": rows,
            "time": int(time.time()),
        }

    def capabilities(self) -> dict[str, Any]:
        snapshot = self.catalog.public_snapshot(self.eligible_v2_providers)
        ready, _ = self.readiness()
        capabilities = snapshot.get("capabilities") if isinstance(snapshot.get("capabilities"), dict) else {}
        video = capabilities.get("video") if isinstance(capabilities.get("video"), dict) else {}
        video["traffic_enabled"] = ready
        video["billing_contract_version"] = VIDEO_BILLING_V2_CONTRACT
        video["reference_contract_version"] = VIDEO_REFERENCE_V22_CONTRACT
        for row in video.get("models") or []:
            if isinstance(row, dict):
                model_id = str(row.get("id") or "")
                resolutions = [str(value) for value in row.get("resolutions") or []]
                model = self.catalog.model(model_id)
                reference_video_resolutions = sorted({
                    route.resolution
                    for route in model.routes
                    if route.enabled
                    and route.provider in self.eligible_v2_providers
                    and route.supports_reference_video
                    and route.resolution
                })
                reference_audio_resolutions = sorted({
                    route.resolution
                    for route in model.routes
                    if route.enabled
                    and route.provider in self.eligible_v2_providers
                    and route.supports_reference_audio
                    and route.resolution
                })
                video_available = bool(self.config.v22_reference_video_enabled and reference_video_resolutions)
                audio_available = bool(self.config.v22_reference_audio_enabled and reference_audio_resolutions)
                combined_resolutions = sorted(set(reference_video_resolutions) & set(reference_audio_resolutions))
                combined_available = bool(self.config.v22_reference_combined_enabled and combined_resolutions)
                row["reference_video"] = {
                    "supported": True,
                    "available": video_available,
                    "reason": "" if video_available else "reference_video_route_disabled",
                    "available_resolutions": [value for value in resolutions if value in reference_video_resolutions],
                    "roles": ["reference_video"],
                    "max_count": 3,
                    "mime_types": ["video/mp4"],
                    "supports_images_with_video": True,
                    "supports_audio_with_video": True,
                    "supports_generate_audio_with_video": True,
                    "max_total_assets": 12,
                }
                row["reference_audio"] = {
                    "supported": True,
                    "available": audio_available,
                    "reason": "" if audio_available else "reference_audio_route_disabled",
                    "available_resolutions": [value for value in resolutions if value in reference_audio_resolutions],
                    "roles": ["reference_audio"],
                    "max_count": 3,
                    "mime_types": ["audio/mpeg", "audio/wav", "audio/aac", "audio/mp4"],
                    "audio_codecs": ["mp3", "wav", "aac", "m4a"],
                    "requires_non_audio_input": True,
                    "supports_images_with_audio": True,
                    "supports_video_with_audio": True,
                    "supports_generate_audio_with_reference_audio": True,
                    "max_total_assets": 12,
                }
                row["reference_video_audio"] = {
                    "supported": True,
                    "available": combined_available,
                    "reason": "" if combined_available else "reference_combined_route_disabled",
                    "available_resolutions": [value for value in resolutions if value in combined_resolutions],
                }
        video["generate_audio"] = bool(video.get("generate_audio"))
        video["settlement_capabilities"] = [
            DURABLE_REQUEST_ID_SUBMIT_CONTRACT,
            "provider-actual-cost-settlement-v1",
            "authenticated-billing-evidence-v1",
        ]
        snapshot["billing_contract_version"] = VIDEO_BILLING_V2_CONTRACT
        return snapshot

    def price_pairs(self) -> list[tuple[str, str]]:
        snapshot = self.catalog.public_snapshot(self.eligible_v2_providers)
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
        reference_rows = []
        for model, resolution in self.price_pairs():
            without_video = self.pricing.official_quote(
                model, resolution, 1, input_rate_class="without_video_input"
            )
            with_video = self.pricing.official_quote(
                model, resolution, 1, input_rate_class="with_video_input"
            )
            reference_rows.append({
                "model": model,
                "resolution": resolution,
                "currency": "CNY",
                "billing_unit": "output_second",
                "reference_video": {
                    "supported": True,
                    "official_rate_class": "with_video_input",
                    "cny_per_second_exact": with_video["cny_per_second_exact"],
                },
                "reference_audio": {
                    "supported": True,
                    "official_rate_class": "without_video_input",
                    "cny_per_second_exact": without_video["cny_per_second_exact"],
                },
                "reference_video_audio": {
                    "supported": True,
                    "official_rate_class": "with_video_input",
                    "cny_per_second_exact": with_video["cny_per_second_exact"],
                },
            })
        return {
            "ok": True,
            "protocol_version": self.catalog.protocol_version,
            "catalog_revision": self.catalog.revision,
            "pricing": self.pricing.snapshot(self.price_pairs()),
            "billing_v2_pricing": self.pricing.official_snapshot(self.price_pairs()),
            "billing_v22_input_profiles": {
                "contract_version": VIDEO_REFERENCE_V22_CONTRACT,
                "pricing_revision": "ark-official-input-mode-1.5-2026-08-13",
                "pricing_mode": "ark_official_input_mode_1_5",
                "reference_video": {"supported": True, "official_rate_class": "with_video_input"},
                "reference_audio": {"supported": True, "official_rate_class": "without_video_input"},
                "reference_video_audio": {"supported": True, "official_rate_class": "with_video_input"},
                "models": reference_rows,
            },
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

    def submit(
        self,
        raw: Any,
        *,
        billing_v2: bool = False,
        reference_v22: bool = False,
    ) -> tuple[dict[str, Any], bool]:
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
            billing_v2=billing_v2,
            reference_v22=reference_v22,
        )
        route = routes[0]
        selection_reason = (
            "capability_only_v1" if len(routes) == 1 else "capability_and_estimated_cost_v1"
        )
        route_plan = [
            {
                "provider_id": candidate.provider,
                "upstream_model": candidate.upstream_model,
                "adapter_revision": candidate.adapter_revision,
                "send_resolution": candidate.send_resolution,
                "billing_mode": candidate.billing_mode,
                "routing_unit_cost": candidate.routing_unit_cost,
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
                    selection_reason=selection_reason,
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
                selection_reason=selection_reason,
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
        billing_v2: bool = False,
        reference_v22: bool = False,
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
                (
                    self.eligible_v2_providers
                    if billing_v2 and configured_providers is None
                    else self.configured_providers
                    if configured_providers is None
                    else configured_providers
                ),
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
        generate_audio = parameters.get("generate_audio") if "generate_audio" in parameters else None
        if generate_audio is True and not billing_v2:
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "video_generate_audio_not_enabled",
                "The legacy video relay contract does not enable generated audio.",
            )
        if generate_audio is True:
            candidate_routes = tuple(candidate for candidate in candidate_routes if candidate.supports_generate_audio)
            if not candidate_routes:
                raise GatewayError(
                    HTTPStatus.CONFLICT,
                    "video_generate_audio_route_unavailable",
                    "No configured video upstream can preserve the requested generated audio.",
                )
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
        audios = _normalize_assets(input_data.get("audios") or raw.get("audios"), "audio", ALLOWED_AUDIO_ROLES)
        candidate_routes = tuple(
            candidate
            for candidate in candidate_routes
            if (not candidate.aspect_ratios or aspect_ratio in candidate.aspect_ratios)
            and (
                not candidate.max_total_assets
                or len(images) + len(videos) + len(audios) <= candidate.max_total_assets
            )
            and (not reference_v22 or not videos or candidate.supports_reference_video)
            and (not reference_v22 or not audios or candidate.supports_reference_audio)
            and (not audios or not candidate.max_reference_audios or len(audios) <= candidate.max_reference_audios)
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
        if route.max_total_assets and len(images) + len(videos) + len(audios) > route.max_total_assets:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_route_reference_limit", "参考素材总数超过当前上游线路限制。")
        if mode in {"first_frame", "reference"} and not images:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_reference_image_required", "当前视频模式至少需要一张参考图片。")
        if mode == "first_last_frame":
            roles = {item["role"] for item in images}
            if not {"first", "last"}.issubset(roles):
                raise GatewayError(HTTPStatus.BAD_REQUEST, "video_first_last_required", "首尾帧模式需要明确的首帧和尾帧图片。")
        if mode == "all_reference" and not images and not videos and not audios:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_reference_required", "全能参考模式至少需要一个参考素材。")
        normalized = {
            "model": stable_model if legacy_alias else model.id,
            "prompt": prompt,
            "mode": mode,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "images": images,
            "videos": videos,
            "audios": audios,
            "generate_audio": bool(parameters.get("generate_audio")) if "generate_audio" in parameters else None,
            "negative_prompt": str(parameters.get("negative_prompt") or "").strip()[:2000],
            "delivery": {"prefer_direct_url": True},
        }
        if reference_v22 and isinstance(input_data.get("reference_input"), dict):
            normalized["reference_input"] = dict(input_data["reference_input"])
        if billing_v2:
            normalized["_billing_v2"] = True
            normalized["_billing_contract_version"] = (
                VIDEO_REFERENCE_V22_CONTRACT if reference_v22 else VIDEO_BILLING_V2_CONTRACT
            )
        if resolution and (not legacy_alias or requested_resolution):
            normalized["resolution"] = resolution
        fingerprint_payload = normalized
        if billing_v2:
            fingerprint_payload = dict(normalized)
            for name, items in (("images", images), ("videos", videos), ("audios", audios)):
                fingerprint_payload[name] = [
                    {
                        "role": str(item.get("role") or "reference"),
                        **(
                            {"identity": str(item.get("identity") or "")}
                            if str(item.get("identity") or "")
                            else {"url": str(item.get("url") or "")}
                        ),
                    }
                    for item in items
                ]
        canonical = json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        normalized["_route"] = {
            "resolution": resolution,
            "send_resolution": route.send_resolution,
        }
        try:
            normalized["_relay_price"] = (
                self.pricing.official_quote(
                    model.id,
                    resolution,
                    duration,
                    input_rate_class="with_video_input" if reference_v22 and videos else "without_video_input",
                )
                if billing_v2
                else self.pricing.quote(model.id, resolution, duration)
            )
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
                resolution=resolution or "default",
                routes=candidate_routes,
                duration=duration,
            )
        except RoutePlanError as error:
            raise GatewayError(HTTPStatus.CONFLICT, "video_model_unavailable", str(error)) from error
        return request_id, fingerprint, normalized, model, routes

    def submit_v2(self, raw: Any, *, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        if not self.config.public_base_url:
            raise GatewayError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "video_public_base_url_missing",
                "视频计费v2公网地址尚未安全配置。",
                category="service_unavailable",
            )
        if not isinstance(raw, dict):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "payload_invalid", "请求体必须是JSON对象。")
        request_id = str(raw.get("request_id") or "").strip()
        if not request_id or not hmac.compare_digest(request_id, str(idempotency_key or "").strip()):
            raise GatewayError(
                HTTPStatus.CONFLICT,
                "video_idempotency_key_mismatch",
                "Idempotency-Key必须与request_id完全一致。",
            )
        if str(raw.get("provider_id") or "") != "video-aixingtu-api":
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_provider_invalid", "provider_id无效。")
        if "generate_audio" not in raw:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "missing_generate_audio", "generate_audio必须明确传布尔值。")
        if not isinstance(raw.get("generate_audio"), bool):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "invalid_generate_audio", "generate_audio必须是布尔值。")
        if any(raw.get(name) not in (None, "", []) for name in ("video", "videos", "audio", "audios", "reference_videos", "reference_audios")):
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "video_reference_unsupported",
                "xtai-video-billing-v2.1不接受参考视频或参考音频；请先完成v2.2能力与价格门禁。",
            )
        image_values: list[str] = []
        if raw.get("image"):
            image_values.append(str(raw.get("image") or "").strip())
        if isinstance(raw.get("images"), list):
            image_values.extend(str(item or "").strip() for item in raw["images"] if str(item or "").strip())
        if len(image_values) > 9:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_reference_limit", "参考图片数量超过限制。")
        image_identities = raw.get("image_identities")
        if image_identities is None:
            identities: list[str] = []
        elif isinstance(image_identities, list):
            identities = [str(item or "").strip().lower() for item in image_identities]
        else:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_image_identity_invalid", "参考图片身份必须是数组。")
        if identities and (
            len(identities) != len(image_values)
            or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in identities)
        ):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_image_identity_invalid", "参考图片身份与素材不匹配。")
        mode = "text"
        images: list[dict[str, str]] = []
        if image_values:
            mode = "first_frame" if len(image_values) == 1 else "all_reference"
            images = [
                {
                    "url": value,
                    "role": "first" if len(image_values) == 1 else "reference",
                    **({"identity": identities[index]} if identities else {}),
                }
                for index, value in enumerate(image_values)
            ]
        translated = {
            "protocol_version": self.catalog.protocol_version,
            "request_id": request_id,
            "model": str(raw.get("model") or "").strip(),
            "input": {"prompt": str(raw.get("prompt") or "").strip(), "images": images, "videos": []},
            "parameters": {
                "resolution": str(raw.get("resolution") or "").strip().lower(),
                "duration": raw.get("duration"),
                "aspect_ratio": str(raw.get("aspect_ratio") or "16:9").strip(),
                "mode": mode,
                "generate_audio": raw["generate_audio"],
            },
        }
        return self.submit(translated, billing_v2=True)

    def submit_v22(self, raw: Any, *, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        """Create a durable v2.2 job using only exact reference-capable routes."""
        if not self.config.public_base_url:
            raise GatewayError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "video_public_base_url_missing",
                "视频计费v2.2公网地址尚未安全配置。",
                category="service_unavailable",
            )
        if not isinstance(raw, dict):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "payload_invalid", "请求体必须是JSON对象。")
        request_id = str(raw.get("request_id") or "").strip()
        if not request_id or not hmac.compare_digest(request_id, str(idempotency_key or "").strip()):
            raise GatewayError(
                HTTPStatus.CONFLICT,
                "video_idempotency_key_mismatch",
                "Idempotency-Key必须与request_id完全一致。",
            )
        if str(raw.get("provider_id") or "") != "video-aixingtu-api":
            raise GatewayError(HTTPStatus.BAD_REQUEST, "video_provider_invalid", "provider_id无效。")
        if "generate_audio" not in raw:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "missing_generate_audio", "generate_audio必须明确传布尔值。")
        if not isinstance(raw.get("generate_audio"), bool):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "invalid_generate_audio", "generate_audio必须是布尔值。")
        has_video = bool(raw.get("reference_videos"))
        has_audio = bool(raw.get("reference_audios"))
        if not has_video and not has_audio:
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "reference_input_combination_unsupported",
                "v2.2仅用于包含参考视频或参考音频的新任务。",
            )
        try:
            references = validate_reference_payload(raw)
            reference_identity = stable_reference_identity(raw)
        except ReferenceContractError as error:
            status = HTTPStatus.CONFLICT if error.code.endswith("identity_mismatch") else HTTPStatus.BAD_REQUEST
            raise GatewayError(status, error.code, str(error)) from error
        enabled = (
            self.config.v22_reference_combined_enabled
            if has_video and has_audio
            else self.config.v22_reference_video_enabled
            if has_video
            else self.config.v22_reference_audio_enabled
        )
        code = "reference_video_contract_unavailable" if has_video else "reference_audio_contract_unavailable"
        message = "当前参考素材合同或精确上游能力尚未发布；任务未冻结、未提交。"
        if not enabled:
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, code, message, category="service_unavailable")
        image_values: list[str] = []
        if raw.get("image"):
            image_values.append(str(raw.get("image") or "").strip())
        if isinstance(raw.get("images"), list):
            image_values.extend(str(item or "").strip() for item in raw["images"] if str(item or "").strip())
        identities_raw = raw.get("image_identities")
        image_identities = (
            [str(item or "").strip().lower() for item in identities_raw]
            if isinstance(identities_raw, list)
            else []
        )
        if image_values and (
            len(image_identities) != len(image_values)
            or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in image_identities)
        ):
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "video_image_identity_invalid",
                "v2.2参考图片必须逐项提供SHA-256身份。",
            )
        if len(image_values) > 9 or len(image_values) + len(references["reference_videos"]) + len(references["reference_audios"]) > 12:
            raise GatewayError(
                HTTPStatus.BAD_REQUEST,
                "video_asset_count_invalid",
                "v2.2最多接受9张图片，且图片、参考视频、参考音频合计不得超过12项。",
            )
        images = [
            {
                "url": value,
                "role": "first" if len(image_values) == 1 else "reference",
                "identity": image_identities[index],
            }
            for index, value in enumerate(image_values)
        ]
        videos = [
            {"url": item["url"], "role": "reference", "identity": item["sha256"]}
            for item in references["reference_videos"]
        ]
        audios = [
            {"url": item["url"], "role": "reference_audio", "identity": item["sha256"]}
            for item in references["reference_audios"]
        ]
        translated = {
            "protocol_version": self.catalog.protocol_version,
            "request_id": request_id,
            "model": str(raw.get("model") or "").strip(),
            "input": {
                "prompt": str(raw.get("prompt") or "").strip(),
                "images": images,
                "videos": videos,
                "audios": audios,
                "reference_input": reference_identity,
            },
            "parameters": {
                "resolution": str(raw.get("resolution") or "").strip().lower(),
                "duration": raw.get("duration"),
                "aspect_ratio": str(raw.get("aspect_ratio") or "16:9").strip(),
                "mode": "all_reference",
                "generate_audio": raw["generate_audio"],
            },
        }
        existing = self.store.get(request_id=request_id, internal=True)
        if existing:
            return self.submit(translated, billing_v2=True, reference_v22=True)
        if not self.reference_verify_slots.acquire(blocking=False):
            raise GatewayError(
                HTTPStatus.TOO_MANY_REQUESTS,
                "reference_media_verifier_busy",
                "参考素材安全校验繁忙，请使用同一request_id稍后重试。",
                category="rate_limit",
            )
        try:
            try:
                self.reference_verifier.verify_image_origins(image_values)
                self.reference_verifier.verify(references)
            except ReferenceContractError as error:
                status = HTTPStatus.CONFLICT if error.code.endswith("identity_mismatch") else HTTPStatus.BAD_REQUEST
                raise GatewayError(status, error.code, str(error)) from error
            return self.submit(translated, billing_v2=True, reference_v22=True)
        except GatewayError as error:
            if error.code in {"video_route_constraints_unsupported", "video_model_unavailable"}:
                raise GatewayError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    code,
                    "当前精确模型、分辨率和参考素材组合没有可执行且可结算的上游路由。",
                    category="service_unavailable",
                ) from error
            raise
        finally:
            self.reference_verify_slots.release()

    def public_v2_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(snapshot.get("job_id") or "")
        delivery = str(snapshot.get("result_delivery") or "unavailable")
        result_url = ""
        if delivery == "ready" and self.config.public_base_url:
            result_url = f"{self.config.public_base_url}/v1/videos/{urllib.parse.quote(task_id, safe='')}/content"
        billing = dict(snapshot.get("billing") or {})
        result = {
            "id": task_id,
            "request_id": str(snapshot.get("request_id") or ""),
            "object": "video",
            "model": str(snapshot.get("model") or ""),
            "status": str(snapshot.get("status") or ""),
            "progress": 100 if str(snapshot.get("status") or "") in {"succeeded", "failed"} else 0,
            "created_at": int(snapshot.get("created_at") or 0),
            "completed_at": int(snapshot.get("finished_at") or 0) or None,
            "result_delivery": delivery,
            "result": {"type": "url", "url": result_url} if result_url else None,
            "result_url": result_url or None,
            "billing": billing,
            "usage": None,
        }
        if isinstance(snapshot.get("input"), dict):
            result["input"] = dict(snapshot["input"])
        return result

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
                if bool(payload.get("_billing_v2")):
                    collector = self.billing_collectors.get(str(job.get("provider_id") or ""))
                    if collector is None or not bool(
                        getattr(collector, "identity_ready", getattr(collector, "ready", False))
                    ):
                        error = _error(
                            "provider_billing_not_ready",
                            "service_unavailable",
                            "The selected video provider cannot prove exact task-level cost.",
                            503,
                            True,
                            False,
                            "validate",
                        )
                        if self.store.advance_route(job_id, error=error):
                            continue
                        self.store.finish(job_id, "failed", error=error)
                        return
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
                submission_request_id = str(job.get("_submission_request_id") or job["request_id"])
                observation = adapter.submit(
                    submission_request_id,
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
                    self.store.begin_recovery(
                        job_id,
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
                if error.uncertain:
                    self.store.begin_recovery(job_id, error=contract)
                else:
                    self.store.finish(job_id, "failed", error=contract)
                return
            except Exception as error:
                self.store.begin_recovery(
                    job_id,
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
            for job in self.store.due_recovery_jobs(limit=self.config.settlement_query_concurrency, lease_seconds=60):
                threading.Thread(
                    target=self._recover_one,
                    args=(job,),
                    daemon=True,
                    name=f"video-recover-{str(job['job_id'])[-8:]}",
                ).start()
            for job in self.store.due_settlement_jobs(
                set(self.billing_collectors),
                limit=self.config.settlement_query_concurrency,
                lease_seconds=300,
            ):
                threading.Thread(
                    target=self._collect_settlement_one,
                    args=(job,),
                    daemon=True,
                    name=f"video-settle-{str(job['job_id'])[-8:]}",
                ).start()
            if self.config.webhook_enabled:
                for event in self.store.due_webhook_events(limit=20, lease_seconds=30):
                    threading.Thread(
                        target=self._deliver_webhook,
                        args=(event,),
                        daemon=True,
                        name=f"video-webhook-{str(event['event_id'])[-8:]}",
                    ).start()

    def _collect_settlement_one(self, job: dict[str, Any]) -> None:
        with self.settlement_slots:
            job_id = str(job.get("job_id") or "")
            provider_id = str(job.get("provider_id") or "").strip().lower()
            collector = self.billing_collectors.get(provider_id)
            if collector is None:
                self.store.retry_settlement_collection(
                    job_id,
                    delay_seconds=3600,
                    error_code="provider_billing_collector_not_configured",
                )
                return
            try:
                current = self.store.get(job_id=job_id, internal=True)
                if not current or str(current.get("billing_status") or "") != "settlement_pending":
                    return
                if provider_id == "paisio":
                    binding = self.store.get_provider_task_binding(job_id)
                    if binding:
                        record = collector.collect(str(binding.get("billing_task_id") or ""))
                    else:
                        record = collector.resolve_and_collect(current)
                        if record.execution_task_id != record.provider_task_id:
                            self.store.bind_provider_task(
                                job_id=job_id,
                                provider_id=provider_id,
                                execution_task_id=record.execution_task_id,
                                billing_task_id=record.provider_task_id,
                                resolver_version=record.resolver_version,
                                provider_record_id=record.provider_record_id,
                                provider_submit_time=record.provider_submit_time,
                                provider_finish_time=record.provider_finish_time,
                                media_size_bytes=record.media_size_bytes,
                                media_sha256=record.media_sha256,
                            )
                else:
                    record = collector.collect(str(current.get("upstream_task_id") or ""))
                aggregate_cost = self.store.record_success_attempt_cost(job_id, record)
                current = self.store.get(job_id=job_id, internal=True)
                if not current or str(current.get("billing_status") or "") != "settlement_pending":
                    return
                evidence = build_settlement_evidence(
                    job_id=job_id,
                    revision=int(current.get("settlement_revision") or 0) + 1,
                    provider_task_id=record.provider_task_id,
                    actual_cost_status="zero_verified" if Decimal(aggregate_cost) == 0 else "actual",
                    actual_cost_cny_exact=aggregate_cost,
                    evidence_source="xtai_aggregate_attempt_cost",
                    evidence_id="aggregate:" + hashlib.sha256(
                        f"{job_id}\0{aggregate_cost}\0{record.evidence_id}".encode("utf-8")
                    ).hexdigest(),
                    observed_at=record.observed_at,
                    contract_version=str(current.get("billing_contract_version") or ""),
                )
                self.store.apply_settlement(evidence)
            except BillingCollectionError as error:
                attempts = max(1, int(job.get("settlement_query_attempts") or 1))
                backoff = self.config.settlement_query_interval_seconds * (2 ** min(attempts - 1, 5))
                self.store.retry_settlement_collection(
                    job_id,
                    delay_seconds=max(error.retry_after_seconds, min(backoff, 3600)),
                    error_code=error.code,
                )
            except StoreConflict:
                current = self.store.get(job_id=job_id, internal=True) or {}
                if str(current.get("billing_status") or "") == "settlement_pending":
                    self.store.retry_settlement_collection(
                        job_id,
                        delay_seconds=900,
                        error_code="provider_billing_settlement_conflict",
                    )
            except Exception:
                self.store.retry_settlement_collection(
                    job_id,
                    delay_seconds=900,
                    error_code="provider_billing_collection_internal_error",
                )

    def _deliver_webhook(self, event: Mapping[str, Any]) -> None:
        event_id = str(event.get("event_id") or "")
        attempt = max(1, int(event.get("attempts") or 1))
        body = str(event.get("payload_json") or "").encode("utf-8")
        try:
            event_payload = json.loads(body.decode("utf-8"))
            contract_version = str((((event_payload.get("data") or {}).get("billing") or {}).get("contract_version") or ""))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            contract_version = ""
        if contract_version not in BILLING_CONTRACT_VERSIONS:
            self.store.retry_webhook(event_id, delay_seconds=3600, error="invalid persisted contract")
            return
        timestamp = str(int(time.time()))
        signature = "v1=" + hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            self.config.webhook_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-XingTu-Contract-Version": contract_version,
                "X-XingTu-Event-Id": event_id,
                "X-XingTu-Timestamp": timestamp,
                "X-XingTu-Delivery-Attempt": str(attempt),
                "X-XingTu-Signature": signature,
                "User-Agent": "XingTuVideoWebhook/1",
            },
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request,
                timeout=self.config.webhook_timeout_seconds,
            ) as response:
                status = int(getattr(response, "status", 0) or 0)
                response.read(4096)
            if not 200 <= status < 300:
                raise OSError(f"webhook returned HTTP {status}")
            self.store.mark_webhook_delivered(event_id)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            delay = min(3600, 5 * (2 ** min(attempt - 1, 9)))
            status = int(getattr(error, "code", 0) or 0)
            label = f"HTTP {status}" if status else type(error).__name__
            self.store.retry_webhook(event_id, delay_seconds=delay, error=label)

    def _poll_one(self, job: dict[str, Any]) -> None:
        with self.poll_slots:
            job_id = str(job["job_id"])
            if int(time.time()) - int(job.get("created_at") or 0) > self.config.max_job_age_seconds:
                self.store.begin_recovery(
                    job_id,
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
                    self.store.begin_recovery(
                        job_id,
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
                self.store.begin_recovery(
                    job_id,
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

    def _recover_one(self, job: dict[str, Any]) -> None:
        """Automatically close failed/uncertain attempts from authenticated evidence."""
        with self.settlement_slots:
            job_id = str(job.get("job_id") or "")
            current = self.store.get(job_id=job_id, internal=True) or {}
            if str(current.get("status") or "") != "reconciling":
                return
            now = int(time.time())
            deadline = int(current.get("recovery_deadline_at") or 0)
            task_id = str(current.get("upstream_task_id") or "")
            if not task_id:
                if int(current.get("recovery_attempts") or 0) <= 2 and self.store.retry_uncertain_submit(job_id):
                    self.start_submit(job_id)
                    return
                if deadline and now >= deadline:
                    self.store.finish(
                        job_id,
                        "failed",
                        error=_error(
                            "video_submit_identity_unresolved",
                            "upstream",
                            "The upstream submit identity could not be proven before the automatic recovery deadline.",
                            504,
                            False,
                            True,
                            "reconcile",
                        ),
                    )
                    return
                self.store.retry_recovery(
                    job_id,
                    delay_seconds=min(3600, 60 * (2 ** min(int(current.get("recovery_attempts") or 1), 5))),
                    error_code="provider_submit_identity_pending",
                )
                return
            provider_id = str(current.get("provider_id") or "").strip().lower()
            collector = self.billing_collectors.get(provider_id)
            if collector is None or not hasattr(collector, "collect_failed"):
                self.store.retry_recovery(job_id, delay_seconds=3600, error_code="provider_failure_collector_not_configured")
                return
            try:
                record = collector.collect_failed(task_id)
                raw_error = json.loads(str(current.get("error_json") or "{}"))
                advanced = self.store.complete_failed_attempt(
                    job_id,
                    provider_task_id=record.provider_task_id,
                    actual_cost_cny_exact=record.actual_cost_cny_exact,
                    evidence_source=record.evidence_source,
                    evidence_id=record.evidence_id,
                    observed_at=record.observed_at,
                    error=raw_error if isinstance(raw_error, dict) else {},
                )
                if advanced:
                    self.start_submit(job_id)
            except BillingCollectionError as error:
                if deadline and now >= deadline:
                    self.store.finish(
                        job_id,
                        "failed",
                        error=_error(
                            "video_recovery_deadline_exceeded",
                            "upstream",
                            "Automatic provider evidence reconciliation reached its deadline.",
                            504,
                            False,
                            True,
                            "reconcile",
                        ),
                        upstream_task_id=task_id,
                        upstream_status=str(current.get("upstream_status") or ""),
                    )
                else:
                    self.store.retry_recovery(job_id, delay_seconds=error.retry_after_seconds, error_code=error.code)
            except (StoreConflict, json.JSONDecodeError):
                self.store.retry_recovery(job_id, delay_seconds=900, error_code="video_recovery_evidence_conflict")

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

    def apply_settlement(self, raw: Any) -> tuple[dict[str, Any], bool]:
        try:
            return self.store.apply_settlement(raw)
        except StoreConflict as error:
            raise GatewayError(
                HTTPStatus.CONFLICT,
                "video_settlement_conflict",
                str(error),
                category="billing",
            ) from error

    def result_source(self, job_id: str, *, require_settled: bool = False) -> tuple[str, ProviderConfig] | None:
        job = self.store.get(job_id=job_id, internal=True)
        if not job or job.get("status") != "succeeded" or not job.get("result_json"):
            return None
        if require_settled and str(job.get("billing_status") or "") != "settled":
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
            v2_content_match = re.fullmatch(r"/v1/videos/(vjob_[0-9a-f]{32})/content", parsed.path)
            if v2_content_match:
                if not self.v2_contract_supported():
                    self.json_response(HTTPStatus.BAD_REQUEST, {"error": _error("video_contract_invalid", "validation", "视频计费协议版本无效。", 400, False, False, "validate")})
                    return
                snapshot = gateway.store.get(job_id=v2_content_match.group(1))
                if snapshot is None:
                    self.json_response(HTTPStatus.NOT_FOUND, {"error": _error("job_not_found", "validation", "视频任务不存在。", 404, False, False, "poll")})
                    return
                if str((snapshot.get("billing") or {}).get("contract_version") or "") != self.v2_contract_version():
                    self.json_response(HTTPStatus.CONFLICT, {"error": _error("task_contract_mismatch", "validation", "任务合同版本不匹配。", 409, False, False, "poll")})
                    return
                try:
                    _stream_result(self, gateway, v2_content_match.group(1), head_only=self.command == "HEAD", require_settled=True)
                except GatewayError as error:
                    self.json_response(error.status, {"error": error.contract()})
                return
            v2_task_match = re.fullmatch(r"/v1/videos/(vjob_[0-9a-f]{32})", parsed.path)
            if v2_task_match:
                if not self.v2_contract_supported():
                    self.json_response(HTTPStatus.BAD_REQUEST, {"error": _error("video_contract_invalid", "validation", "视频计费协议版本无效。", 400, False, False, "validate")})
                    return
                snapshot = gateway.store.get(job_id=v2_task_match.group(1))
                if snapshot is None:
                    self.json_response(HTTPStatus.NOT_FOUND, {"error": _error("job_not_found", "validation", "视频任务不存在。", 404, False, False, "poll")})
                    return
                if str((snapshot.get("billing") or {}).get("contract_version") or "") != self.v2_contract_version():
                    self.json_response(
                        HTTPStatus.CONFLICT,
                        {"error": _error("task_contract_mismatch", "validation", "任务合同版本不匹配。", 409, False, False, "poll")},
                    )
                    return
                self.json_response(HTTPStatus.OK, gateway.public_v2_snapshot(snapshot))
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
            path = urllib.parse.urlsplit(self.path).path
            if path not in {"/v1/video-jobs", "/v1/videos", "/v1/operations/video-settlements"}:
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
                if path == "/v1/operations/video-settlements":
                    snapshot, reused = gateway.apply_settlement(raw)
                elif path == "/v1/videos":
                    contract_version = self.v2_contract_version()
                    if hmac.compare_digest(contract_version, VIDEO_BILLING_V2_CONTRACT):
                        snapshot, reused = gateway.submit_v2(
                            raw,
                            idempotency_key=str(self.headers.get("Idempotency-Key") or ""),
                        )
                    elif hmac.compare_digest(contract_version, VIDEO_REFERENCE_V22_CONTRACT):
                        snapshot, reused = gateway.submit_v22(
                            raw,
                            idempotency_key=str(self.headers.get("Idempotency-Key") or ""),
                        )
                    else:
                        raise GatewayError(HTTPStatus.BAD_REQUEST, "unsupported_contract_version", "视频任务合同版本无效。")
                    self.json_response(HTTPStatus.OK if reused else HTTPStatus.ACCEPTED, gateway.public_v2_snapshot(snapshot))
                    return
                else:
                    snapshot, reused = gateway.submit(raw)
                self.json_response(HTTPStatus.ACCEPTED, {"ok": True, "reused": reused, "job": snapshot})
            except GatewayError as error:
                self.json_response(error.status, {"error": error.contract()})
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.json_response(HTTPStatus.BAD_REQUEST, {"error": _error("json_invalid", "validation", "请求体不是有效JSON。", 400, False, False, "validate")})

        def v2_contract_version(self) -> str:
            return str(self.headers.get("X-XingTu-Contract-Version") or "").strip()

        def v2_contract_supported(self) -> bool:
            return self.v2_contract_version() in BILLING_CONTRACT_VERSIONS

        def v2_contract_current(self) -> bool:
            supplied = self.v2_contract_version()
            return bool(supplied) and hmac.compare_digest(supplied, VIDEO_BILLING_V2_CONTRACT)

    return Handler


def _stream_result(
    handler: BaseHTTPRequestHandler,
    gateway: Gateway,
    job_id: str,
    *,
    head_only: bool,
    require_settled: bool = False,
) -> None:
    if not gateway.stream_slots.acquire(blocking=False):
        raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "result_stream_busy", "视频中转流式并发已满。", category="service_unavailable")
    gateway.stream_started()
    transferred = 0
    succeeded = False
    try:
        source = gateway.result_source(job_id, require_settled=require_settled)
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
        identity = str(source.get("identity") or "").strip().lower()
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
        if identity and not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise GatewayError(HTTPStatus.BAD_REQUEST, f"video_{kind}_identity_invalid", "Reference material identity is invalid.")
        result.append({
            "url": url,
            "role": role,
            **({"identity": identity} if identity else {}),
        })
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


def _validated_public_base_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("VIDEO_JOB_GATEWAY_PUBLIC_BASE_URL must be an HTTPS origin on port 443")
    return urllib.parse.urlunsplit(("https", parsed.netloc, "", "", ""))


def _validated_webhook_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    try:
        literal = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        literal = None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or literal is not None
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("VIDEO_JOB_GATEWAY_WEBHOOK_URL must use an HTTPS domain on port 443 without query parameters")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path or "/", "", ""))


def _validated_provider_billing_url(raw: str, provider_base_url: str) -> str:
    value = str(raw or "").strip()
    parsed = urllib.parse.urlsplit(value)
    provider = urllib.parse.urlsplit(str(provider_base_url or "").strip())
    try:
        literal = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        literal = None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or literal is not None
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.hostname.lower().rstrip(".") != str(provider.hostname or "").lower().rstrip(".")
    ):
        raise RuntimeError("Toonflow billing URL must use the configured provider HTTPS domain on port 443")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))


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


def _env_bool(name: str, default: bool) -> bool:
    value = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def main() -> None:
    config = Config.from_env()
    gateway = Gateway(config)
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), handler_class(gateway))
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
