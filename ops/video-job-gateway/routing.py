"""Deterministic, provider-neutral route planning for video jobs."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from catalog import Route


class RoutePlanError(ValueError):
    """The eligible route set cannot produce a safe provider plan."""


def build_route_plan(
    *,
    request_id: str,
    stable_model: str,
    resolution: str,
    routes: Iterable[Route],
    duration: int = 0,
) -> tuple[Route, ...]:
    """Return an immutable, deterministic route order grouped by priority tier.

    Equal-priority providers are ranked with rendezvous hashing. The result is
    independent of catalog input order, requires at most one route per provider,
    and can be persisted before any upstream submission.
    """

    identity = (
        str(request_id or "").strip(),
        str(stable_model or "").strip(),
        str(resolution or "").strip().lower(),
    )
    if not all(identity):
        raise RoutePlanError("route plan identity is incomplete")

    candidates = tuple(routes)
    if not candidates:
        raise RoutePlanError("route plan has no eligible provider")

    route_keys: set[tuple[str, str]] = set()
    tiers: dict[int, list[Route]] = defaultdict(list)
    for route in candidates:
        provider = str(route.provider or "").strip().lower()
        if not provider:
            raise RoutePlanError("route plan contains an invalid provider")
        route_key = (provider, str(route.upstream_model or "").strip())
        if route_key in route_keys:
            raise RoutePlanError("route plan contains a duplicate provider/model route")
        route_keys.add(route_key)
        tiers[int(route.priority)].append(route)

    ordered: list[Route] = []
    for priority in sorted(tiers):
        ordered.extend(
            sorted(
                tiers[priority],
                key=lambda route: (
                    _estimated_cost(route, duration),
                    -_score(identity, route),
                    route.provider,
                    route.upstream_model,
                ),
            )
        )
    return tuple(ordered)


def _estimated_cost(route: Route, duration: int) -> Decimal:
    """Return catalog-only cost used for deterministic upstream route ordering."""

    mode = str(route.billing_mode or "").strip().lower()
    raw = str(route.routing_unit_cost or "").strip()
    if not mode or not raw:
        return Decimal("Infinity")
    try:
        unit = Decimal(raw)
    except InvalidOperation as error:
        raise RoutePlanError("route plan contains an invalid routing cost") from error
    if not unit.is_finite() or unit <= 0:
        raise RoutePlanError("route plan contains an invalid routing cost")
    if mode == "per_second":
        seconds = max(1, int(duration or 0))
        return unit * Decimal(seconds)
    if mode == "per_call":
        return unit
    raise RoutePlanError("route plan contains an unsupported billing mode")


def _score(identity: tuple[str, str, str], route: Route) -> int:
    material = "\0".join(
        (*identity, route.provider, route.upstream_model, route.adapter_revision)
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
