"""Validated, immutable model catalog for the XingTu video relay."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CatalogError(ValueError):
    """The checked-in relay catalog is invalid or cannot route a request."""


@dataclass(frozen=True, slots=True)
class Route:
    provider: str
    upstream_model: str
    priority: int
    enabled: bool
    adapter_revision: str
    resolution: str = ""
    send_resolution: bool = False
    aspect_ratios: tuple[str, ...] = ()
    max_total_assets: int = 0


@dataclass(frozen=True, slots=True)
class Model:
    id: str
    label: str
    enabled: bool
    operation_modes: tuple[str, ...]
    aspect_ratios: tuple[str, ...]
    durations: tuple[int, ...]
    duration_min: int
    duration_max: int
    max_images: int
    max_videos: int
    routes: tuple[Route, ...]
    resolutions: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()


class Catalog:
    def __init__(self, protocol_version: str, revision: str, models: tuple[Model, ...]) -> None:
        self.protocol_version = protocol_version
        self.revision = revision
        self.models = models
        self._by_id = {item.id: item for item in models}
        self._aliases: dict[str, tuple[Model, str]] = {}
        for item in models:
            for alias, resolution in item.aliases:
                if alias in self._by_id or alias in self._aliases:
                    raise CatalogError(f"video relay catalog contains a duplicate model alias: {alias!r}")
                self._aliases[alias] = (item, resolution)

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CatalogError(f"video relay catalog cannot be read: {type(error).__name__}") from error
        if not isinstance(raw, dict):
            raise CatalogError("video relay catalog must be a JSON object")
        protocol_version = str(raw.get("protocol_version") or "").strip()
        revision = str(raw.get("revision") or "").strip()
        if protocol_version != "xtai-relay-v1" or not revision:
            raise CatalogError("video relay catalog protocol_version or revision is invalid")
        rows = raw.get("models")
        if not isinstance(rows, list) or not rows:
            raise CatalogError("video relay catalog models must be a non-empty array")
        models: list[Model] = []
        seen: set[str] = set()
        for source in rows:
            if not isinstance(source, dict):
                raise CatalogError("video relay catalog model entry must be an object")
            model_id = str(source.get("id") or "").strip()
            if not model_id or model_id in seen or len(model_id) > 160:
                raise CatalogError(f"video relay catalog contains an invalid or duplicate model id: {model_id!r}")
            seen.add(model_id)
            model_enabled = bool(source.get("enabled", False))
            capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
            operation_modes = tuple(_strings(capabilities.get("operation_modes"), 32))
            aspect_ratios = tuple(_strings(capabilities.get("aspect_ratios"), 20))
            resolutions = tuple(value.lower() for value in _strings(capabilities.get("resolutions"), 20))
            durations = tuple(sorted(set(_integers(capabilities.get("durations"), 1, 120))))
            duration_min = _bounded_int(capabilities.get("duration_min"), 0, 120)
            duration_max = _bounded_int(capabilities.get("duration_max"), 0, 120)
            if durations:
                duration_min, duration_max = min(durations), max(durations)
            if duration_min and duration_max and duration_min > duration_max:
                raise CatalogError(f"video relay catalog duration range is invalid for {model_id}")
            route_rows = source.get("routes")
            if not isinstance(route_rows, list) or (model_enabled and not route_rows):
                raise CatalogError(f"video relay catalog routes are missing for {model_id}")
            routes: list[Route] = []
            route_keys: set[tuple[str, str, str]] = set()
            for route_source in route_rows:
                if not isinstance(route_source, dict):
                    raise CatalogError(f"video relay route is invalid for {model_id}")
                provider = str(route_source.get("provider") or "").strip().lower()
                upstream_model = str(route_source.get("upstream_model") or "").strip()
                route_resolution = str(route_source.get("resolution") or "").strip().lower()[:20]
                route_aspect_ratios = tuple(_strings(route_source.get("aspect_ratios"), 20))
                if provider not in {"paisio", "rolldek", "toonflow"} or not upstream_model:
                    raise CatalogError(f"video relay route provider/model is invalid for {model_id}")
                if route_resolution and resolutions and route_resolution not in resolutions:
                    raise CatalogError(f"video relay route resolution is invalid for {model_id}")
                if route_aspect_ratios and aspect_ratios and not set(route_aspect_ratios).issubset(aspect_ratios):
                    raise CatalogError(f"video relay route aspect ratio is invalid for {model_id}")
                key = (provider, upstream_model, route_resolution)
                if key in route_keys:
                    raise CatalogError(f"video relay catalog contains a duplicate route for {model_id}")
                route_keys.add(key)
                routes.append(
                    Route(
                        provider=provider,
                        upstream_model=upstream_model,
                        priority=_bounded_int(route_source.get("priority"), 0, 10000),
                        enabled=bool(route_source.get("enabled", True)),
                        adapter_revision=str(route_source.get("adapter_revision") or "v1").strip()[:40] or "v1",
                        resolution=route_resolution,
                        send_resolution=bool(route_source.get("send_resolution", False)),
                        aspect_ratios=route_aspect_ratios,
                        max_total_assets=_bounded_int(route_source.get("max_total_assets"), 0, 30),
                    )
                )
            routes.sort(key=lambda item: (item.priority, item.provider, item.upstream_model))
            models.append(
                Model(
                    id=model_id,
                    label=str(source.get("label") or model_id).strip()[:120] or model_id,
                    enabled=model_enabled,
                    operation_modes=operation_modes,
                    aspect_ratios=aspect_ratios,
                    durations=durations,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    max_images=_bounded_int(capabilities.get("max_images"), 0, 20),
                    max_videos=_bounded_int(capabilities.get("max_videos"), 0, 10),
                    routes=tuple(routes),
                    resolutions=resolutions,
                    aliases=tuple(_aliases(source.get("aliases"), resolutions, model_id)),
                )
            )
        return cls(protocol_version, revision, tuple(models))

    def model(self, model_id: str) -> Model:
        item = self._by_id.get(str(model_id or "").strip())
        if not item:
            raise CatalogError("stable video model is not in the approved catalog")
        if not item.enabled:
            raise CatalogError("stable video model is not enabled")
        return item

    def select_route(self, model_id: str, configured_providers: set[str]) -> tuple[Model, Route]:
        item = self.model(model_id)
        route = next(
            (
                candidate
                for candidate in item.routes
                if candidate.enabled and candidate.provider in configured_providers
            ),
            None,
        )
        if route is None:
            raise CatalogError("stable video model has no configured upstream route")
        return item, route

    def resolve_request(
        self,
        model_id: str,
        resolution: str,
        configured_providers: set[str],
    ) -> tuple[Model, Route, str, bool]:
        item, routes, requested_resolution, legacy_alias = self.resolve_routes(
            model_id,
            resolution,
            configured_providers,
        )
        return item, routes[0], requested_resolution, legacy_alias

    def resolve_routes(
        self,
        model_id: str,
        resolution: str,
        configured_providers: set[str],
    ) -> tuple[Model, tuple[Route, ...], str, bool]:
        requested_model = str(model_id or "").strip()
        requested_resolution = str(resolution or "").strip().lower()
        alias = self._aliases.get(requested_model)
        legacy_alias = alias is not None
        if alias:
            item, forced_resolution = alias
            if requested_resolution and requested_resolution != forced_resolution:
                raise CatalogError("legacy video model alias conflicts with the requested resolution")
            requested_resolution = forced_resolution
        else:
            item = self._by_id.get(requested_model)
            if not item:
                raise CatalogError("stable video model is not in the approved catalog")
        if not item.enabled:
            raise CatalogError("stable video model is not enabled")
        if item.resolutions:
            if not requested_resolution:
                raise CatalogError("stable video model requires an explicit resolution")
            if requested_resolution not in item.resolutions:
                raise CatalogError("stable video model does not support the requested resolution")
        routes = tuple(
                candidate
                for candidate in item.routes
                if candidate.enabled
                and candidate.provider in configured_providers
                and (not candidate.resolution or candidate.resolution == requested_resolution)
        )
        if not routes:
            raise CatalogError("stable video model/resolution has no configured upstream route")
        return item, routes, requested_resolution, legacy_alias

    def public_snapshot(self, configured_providers: set[str]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for item in self.models:
            active_routes = [
                route
                for route in item.routes
                if route.enabled and route.provider in configured_providers
            ]
            available_resolutions = [
                resolution
                for resolution in item.resolutions
                if any(
                    not route.resolution or route.resolution == resolution
                    for route in active_routes
                )
            ]
            available = bool(item.enabled and (available_resolutions or active_routes))
            if not item.enabled:
                continue
            # Publish only constraints that are safe for every route currently
            # eligible for new traffic.  Provider-specific expansion belongs in
            # the relay catalog and must never require a desktop release.
            safe_aspect_ratios = set(item.aspect_ratios)
            for route in active_routes:
                if route.aspect_ratios:
                    safe_aspect_ratios.intersection_update(route.aspect_ratios)
            available = bool(available and safe_aspect_ratios)
            max_total_assets = item.max_images + item.max_videos
            route_asset_limits = [route.max_total_assets for route in active_routes if route.max_total_assets > 0]
            if route_asset_limits:
                max_total_assets = min(max_total_assets, *route_asset_limits)
            rows.append(
                {
                    "id": item.id,
                    "label": item.label,
                    "available": available,
                    "operation_modes": list(item.operation_modes),
                    "aspect_ratios": [value for value in item.aspect_ratios if value in safe_aspect_ratios],
                    "durations": list(item.durations),
                    "duration_min": item.duration_min,
                    "duration_max": item.duration_max,
                    "max_images": item.max_images,
                    "max_videos": item.max_videos,
                    "max_total_assets": max_total_assets,
                    "resolutions": available_resolutions,
                }
            )
        return {
            "protocol_version": self.protocol_version,
            "revision": self.revision,
            "capabilities": {
                "text": {"managed": True, "traffic_enabled": False},
                "image": {"managed": True, "traffic_enabled": False, "implementation": "image_job_gateway"},
                "video": {"managed": True, "traffic_enabled": False, "models": rows},
            },
        }


def _strings(raw: Any, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for value in raw:
        text = str(value or "").strip()[:limit]
        if text and text not in result:
            result.append(text)
    return result


def _integers(raw: Any, minimum: int, maximum: int) -> list[int]:
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for value in raw:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if minimum <= number <= maximum:
            result.append(number)
    return result


def _aliases(raw: Any, resolutions: tuple[str, ...], model_id: str) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise CatalogError(f"video relay catalog aliases must be an object for {model_id}")
    result: list[tuple[str, str]] = []
    for raw_alias, raw_resolution in raw.items():
        alias = str(raw_alias or "").strip()
        resolution = str(raw_resolution or "").strip().lower()
        if not alias or len(alias) > 160 or not resolution or (resolutions and resolution not in resolutions):
            raise CatalogError(f"video relay catalog contains an invalid alias for {model_id}")
        result.append((alias, resolution))
    return result


def _bounded_int(raw: Any, minimum: int, maximum: int) -> int:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    return max(minimum, min(value, maximum))
