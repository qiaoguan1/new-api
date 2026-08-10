"""Deterministic, provider-neutral route planning for video jobs."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable

from catalog import Route


class RoutePlanError(ValueError):
    """The eligible route set cannot produce a safe provider plan."""


def build_route_plan(
    *,
    request_id: str,
    stable_model: str,
    resolution: str,
    routes: Iterable[Route],
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

    providers: set[str] = set()
    tiers: dict[int, list[Route]] = defaultdict(list)
    for route in candidates:
        provider = str(route.provider or "").strip().lower()
        if not provider:
            raise RoutePlanError("route plan contains an invalid provider")
        if provider in providers:
            raise RoutePlanError("route plan contains a duplicate provider")
        providers.add(provider)
        tiers[int(route.priority)].append(route)

    ordered: list[Route] = []
    for priority in sorted(tiers):
        ordered.extend(
            sorted(
                tiers[priority],
                key=lambda route: (
                    -_score(identity, route),
                    route.provider,
                    route.upstream_model,
                ),
            )
        )
    return tuple(ordered)


def _score(identity: tuple[str, str, str], route: Route) -> int:
    material = "\0".join(
        (*identity, route.provider, route.upstream_model, route.adapter_revision)
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
