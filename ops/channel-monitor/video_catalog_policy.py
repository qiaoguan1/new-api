#!/usr/bin/env python3
"""Fail-closed policy for normalizing upstream video model catalogs.

The stable catalog belongs to the relay protocol. Upstream names are mutable
route attributes and never become public model identifiers by themselves.
"""

from __future__ import annotations

import copy
import math
import re
from collections import defaultdict
from typing import Any, Iterable


FIXED_CATALOG = {
    "seedance-2.0": ("480p", "720p", "1080p"),
    "seedance-2.0-fast": ("480p", "720p"),
    "seedance-2.0-mini": ("480p", "720p"),
}
REVIEW_STATES = {"approved", "pending", "rejected"}
MATCH_TYPES = {"exact", "regex"}
AMBIGUOUS_MARKERS = {
    "discount",
    "official",
    "official1",
    "official2",
    "premium",
    "selfsur",
    "value",
    "fast2",
    "pro",
    "c8",
    "431",
}


class CatalogPolicyError(ValueError):
    """Raised when an operator policy is unsafe or internally inconsistent."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _target(rule: dict[str, Any]) -> tuple[str, str]:
    return rule["stable_model"], rule["resolution"]


def validate_policy(raw_policy: Any) -> dict[str, Any]:
    """Validate and compile a versioned operator policy.

    A new invalid policy must fail before it can replace a previously valid
    manifest. The returned object is an isolated copy with private indexes.
    """
    if not isinstance(raw_policy, dict):
        raise CatalogPolicyError("policy must be a JSON object")
    policy = copy.deepcopy(raw_policy)
    if policy.get("schema_version") != 1:
        raise CatalogPolicyError("schema_version must be 1")
    if not _text(policy.get("revision")):
        raise CatalogPolicyError("revision is required")

    catalog = policy.get("stable_catalog")
    if not isinstance(catalog, dict):
        raise CatalogPolicyError("stable_catalog must be an object")
    normalized_catalog: dict[str, tuple[str, ...]] = {}
    for model, resolutions in catalog.items():
        if model not in FIXED_CATALOG:
            raise CatalogPolicyError(f"unsupported stable model: {model}")
        if not isinstance(resolutions, list) or not resolutions:
            raise CatalogPolicyError(f"stable model {model} requires resolutions")
        values = tuple(_text(item) for item in resolutions)
        if any(not item for item in values) or len(set(values)) != len(values):
            raise CatalogPolicyError(f"invalid resolutions for {model}")
        if set(values) != set(FIXED_CATALOG[model]):
            raise CatalogPolicyError(f"stable model {model} changes the fixed protocol")
        normalized_catalog[model] = values
    if set(normalized_catalog) != set(FIXED_CATALOG):
        raise CatalogPolicyError("stable_catalog must define all three protocol models")

    allowlist = policy.get("publish_allowlist")
    if not isinstance(allowlist, list):
        raise CatalogPolicyError("publish_allowlist must be a list")
    publish_keys: set[tuple[str, str]] = set()
    for entry in allowlist:
        if not isinstance(entry, dict):
            raise CatalogPolicyError("publish_allowlist entries must be objects")
        key = (_text(entry.get("model")), _text(entry.get("resolution")))
        if key[0] not in normalized_catalog or key[1] not in normalized_catalog.get(key[0], ()):
            raise CatalogPolicyError(f"invalid publish_allowlist entry: {key}")
        if key in publish_keys:
            raise CatalogPolicyError(f"duplicate publish_allowlist entry: {key}")
        publish_keys.add(key)

    rules = policy.get("rules")
    if not isinstance(rules, list):
        raise CatalogPolicyError("rules must be a list")
    rule_ids: set[str] = set()
    exact_targets: dict[tuple[str, str], tuple[str, str]] = {}
    compiled_rules: list[dict[str, Any]] = []
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise CatalogPolicyError(f"rule {position} must be an object")
        rule_id = _text(rule.get("id"))
        if not rule_id or rule_id in rule_ids:
            raise CatalogPolicyError(f"invalid or duplicate rule id: {rule_id!r}")
        rule_ids.add(rule_id)
        if (
            not isinstance(rule.get("version"), int)
            or isinstance(rule.get("version"), bool)
            or rule["version"] < 1
        ):
            raise CatalogPolicyError(f"rule {rule_id} requires a positive integer version")
        if not isinstance(rule.get("priority"), int) or isinstance(rule.get("priority"), bool):
            raise CatalogPolicyError(f"rule {rule_id} requires an integer priority")
        if not isinstance(rule.get("enabled"), bool):
            raise CatalogPolicyError(f"rule {rule_id} requires a boolean enabled flag")
        if rule.get("review_state") not in REVIEW_STATES:
            raise CatalogPolicyError(f"rule {rule_id} has invalid review_state")
        source = _text(rule.get("source"))
        match_type = rule.get("match")
        pattern = _text(rule.get("pattern"))
        stable_model = _text(rule.get("stable_model"))
        resolution = _text(rule.get("resolution"))
        if not source or not pattern or match_type not in MATCH_TYPES:
            raise CatalogPolicyError(f"rule {rule_id} has invalid matching fields")
        if (
            stable_model not in normalized_catalog
            or resolution not in normalized_catalog[stable_model]
        ):
            raise CatalogPolicyError(f"rule {rule_id} targets an invalid stable SKU")
        if not _text(rule.get("reason")):
            raise CatalogPolicyError(f"rule {rule_id} requires a reason")

        compiled = copy.deepcopy(rule)
        if match_type == "regex":
            if (
                len(pattern) > 200
                or re.search(r"\\[1-9]|\(\?[=!<]", pattern)
                or re.search(r"\([^)]*[+*][^)]*\)[+*{]", pattern)
            ):
                raise CatalogPolicyError(
                    f"rule {rule_id} uses an unsafe or overly complex regex"
                )
            try:
                compiled["_regex"] = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise CatalogPolicyError(f"rule {rule_id} has invalid regex: {exc}") from exc
        compiled_rules.append(compiled)

        if rule["enabled"] and rule["review_state"] == "approved" and match_type == "exact":
            exact_key = (source.casefold(), pattern.casefold())
            previous = exact_targets.get(exact_key)
            if previous is not None and previous != (stable_model, resolution):
                raise CatalogPolicyError(
                    f"approved exact rules conflict for {source}:{pattern}"
                )
            exact_targets[exact_key] = (stable_model, resolution)

    policy["_catalog"] = normalized_catalog
    policy["_publish_keys"] = publish_keys
    policy["_rules"] = sorted(
        compiled_rules,
        key=lambda rule: (-rule["priority"], rule["id"]),
    )
    policy["_validated"] = True
    return policy


def _ensure_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return policy if policy.get("_validated") is True else validate_policy(policy)


def _base_result(source: str, raw_name: str) -> dict[str, Any]:
    return {
        "status": "review_required",
        "stable_model": None,
        "resolution": None,
        "match_type": None,
        "rule_id": None,
        "reason": "unrecognized or ambiguous upstream model",
        "source": source,
        "raw_name": raw_name,
    }


def _matched_result(
    source: str,
    raw_name: str,
    stable_model: str,
    resolution: str,
    match_type: str,
    *,
    rule_id: str | None = None,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "matched",
        "stable_model": stable_model,
        "resolution": resolution,
        "match_type": match_type,
        "rule_id": rule_id,
        "reason": reason,
        "source": source,
        "raw_name": raw_name,
    }


def _approved_rules(policy: dict[str, Any], match_type: str) -> list[dict[str, Any]]:
    return [
        rule
        for rule in policy["_rules"]
        if rule["enabled"]
        and rule["review_state"] == "approved"
        and rule["match"] == match_type
    ]


def _match_exact(source: str, raw_name: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    folded_name = raw_name.casefold()
    rules = _approved_rules(policy, "exact")
    for rule_source, label in ((source.casefold(), "source_exact"), ("*", "global_exact")):
        matches = [
            rule
            for rule in rules
            if rule["source"].casefold() == rule_source
            and rule["pattern"].casefold() == folded_name
        ]
        if not matches:
            continue
        targets = {_target(rule) for rule in matches}
        if len(targets) != 1:
            return None
        rule = matches[0]
        return _matched_result(
            source,
            raw_name,
            rule["stable_model"],
            rule["resolution"],
            label,
            rule_id=rule["id"],
            reason=rule["reason"],
        )
    return None


def _match_regex(source: str, raw_name: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    matches = [
        rule
        for rule in _approved_rules(policy, "regex")
        if rule["source"] in {"*", source} and rule["_regex"].fullmatch(raw_name)
    ]
    if not matches:
        return None
    best_scope = 1 if any(rule["source"] == source for rule in matches) else 0
    matches = [rule for rule in matches if (1 if rule["source"] == source else 0) == best_scope]
    best_priority = max(rule["priority"] for rule in matches)
    matches = [rule for rule in matches if rule["priority"] == best_priority]
    if len({_target(rule) for rule in matches}) != 1:
        return None
    rule = matches[0]
    return _matched_result(
        source,
        raw_name,
        rule["stable_model"],
        rule["resolution"],
        "reviewed_regex",
        rule_id=rule["id"],
        reason=rule["reason"],
    )


def _conservative_parse(source: str, raw_name: str, policy: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(source, raw_name)
    normalized = raw_name.casefold().strip()
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}

    if re.search(r"(?:seedance|sd)[-_ ]*2(?:[.\-_ ]*5)(?:\D|$)", normalized):
        result["status"] = "rejected"
        result["reason"] = "unsupported Seedance family"
        return result
    family_match = re.search(r"seedance[-_ ]*2(?:[.\-_ ]*0)?(?:\D|$)", normalized)
    short_match = re.search(r"(?:^|[^a-z0-9])sd[-_ ]*2(?:\D|$)", normalized)
    if not family_match and not short_match:
        result["reason"] = "not a recognized Seedance 2.0 family name"
        return result

    resolutions = {f"{value}p" for value in re.findall(r"(?<!\d)(480|720|1080)p(?!\d)", normalized)}
    if len(resolutions) != 1:
        result["reason"] = "exactly one supported resolution is required"
        return result
    resolution = next(iter(resolutions))

    has_fast = "fast" in tokens
    has_mini = "mini" in tokens
    if has_fast and has_mini:
        result["reason"] = "conflicting fast and mini variant markers"
        return result
    ambiguous = sorted(tokens & AMBIGUOUS_MARKERS)
    if ambiguous:
        result["reason"] = f"unreviewed marketing or provider markers: {', '.join(ambiguous)}"
        return result

    stable_model = "seedance-2.0-fast" if has_fast else (
        "seedance-2.0-mini" if has_mini else "seedance-2.0"
    )
    if resolution not in policy["_catalog"][stable_model]:
        result["status"] = "rejected"
        result["reason"] = f"{stable_model} does not allow {resolution}"
        return result
    return _matched_result(
        source,
        raw_name,
        stable_model,
        resolution,
        "conservative_parser",
        reason="unique family, variant, and resolution tokens",
    )


def normalize_model_name(source: str, raw_name: str, policy: dict[str, Any]) -> dict[str, Any]:
    """Map one mutable upstream name to the stable catalog or quarantine it."""
    checked = _ensure_policy(policy)
    clean_source = _text(source)
    clean_name = _text(raw_name)
    if not clean_source or not clean_name or len(clean_name) > 240:
        result = _base_result(clean_source, clean_name)
        result["status"] = "rejected"
        result["reason"] = "source and bounded raw model name are required"
        return result
    return (
        _match_exact(clean_source, clean_name, checked)
        or _match_regex(clean_source, clean_name, checked)
        or _conservative_parse(clean_source, clean_name, checked)
    )


def propose_model_candidate(
    source: str,
    raw_name: str,
    policy: dict[str, Any],
) -> dict[str, Any] | None:
    """Return an untrusted review suggestion without changing mapping state."""
    checked = _ensure_policy(policy)
    if not _text(source) or not _text(raw_name):
        return None
    normalized = raw_name.casefold().strip()
    if re.search(r"(?:seedance|sd)[-_ ]*2(?:[.\-_ ]*5)(?:\D|$)", normalized):
        return None
    family_match = re.search(r"seedance[-_ ]*2[.\-_ ]*0(?:\D|$)", normalized)
    short_match = re.search(r"(?:^|[^a-z0-9])sd[-_ ]*2(?:\D|$)", normalized)
    if not family_match and not short_match:
        return None
    resolutions = {
        f"{value}p"
        for value in re.findall(r"(?<!\d)(480|720|1080)p(?!\d)", normalized)
    }
    if len(resolutions) != 1:
        return None
    resolution = next(iter(resolutions))
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}
    has_fast = "fast" in tokens
    has_mini = "mini" in tokens
    if has_fast and has_mini:
        return None
    stable_model = "seedance-2.0-fast" if has_fast else (
        "seedance-2.0-mini" if has_mini else "seedance-2.0"
    )
    if resolution not in checked["_catalog"][stable_model]:
        return None
    extra_markers = tokens & AMBIGUOUS_MARKERS
    return {
        "stable_model": stable_model,
        "resolution": resolution,
        "confidence": "low" if extra_markers else "medium",
        "requires_review": True,
        "reason": (
            "family, variant, and resolution parsed but extra markers require review"
            if extra_markers
            else "family, variant, and resolution parsed; operator approval still required"
        ),
    }


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def build_manifests(
    mappings: Iterable[dict[str, Any]],
    routes: Iterable[dict[str, Any]],
    prices: Iterable[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Build internal and privacy-safe public manifests from strict gates."""
    checked = _ensure_policy(policy)
    route_index = {
        (
            route.get("channel_id"),
            _text(route.get("source")),
            _text(route.get("raw_model")),
        ): route
        for route in routes
        if isinstance(route, dict)
    }
    trusted_prices: dict[tuple[str, str], dict[str, Any]] = {}
    trusted_sku_prices: dict[tuple[str, str, str], dict[str, Any]] = {}
    for price in prices:
        if not isinstance(price, dict):
            continue
        key = (_text(price.get("source")), _text(price.get("raw_model")))
        if (
            all(key)
            and price.get("trusted") is True
            and _text(price.get("version"))
            and _positive_number(price.get("cost"))
        ):
            trusted_prices[key] = price
            stable_model = _text(price.get("stable_model"))
            resolution = _text(price.get("resolution"))
            if stable_model and resolution:
                trusted_sku_prices[(key[0], stable_model, resolution)] = price

    internal_routes: list[dict[str, Any]] = []
    seen_routes: set[tuple[str, str, Any, str, str]] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict) or mapping.get("status") != "matched":
            continue
        source = _text(mapping.get("source"))
        raw_model = _text(mapping.get("raw_model") or mapping.get("raw_name"))
        stable_model = _text(mapping.get("stable_model"))
        resolution = _text(mapping.get("resolution"))
        channel_id = mapping.get("channel_id")
        stable_key = (stable_model, resolution)
        route_key = (channel_id, source, raw_model)
        route = route_index.get(route_key) or route_index.get((None, source, raw_model))
        price = trusted_prices.get((source, raw_model)) or trusted_sku_prices.get(
            (source, stable_model, resolution)
        )
        if (
            stable_key not in checked["_publish_keys"]
            or route is None
            or route.get("enabled") is not True
            or route.get("healthy") is not True
            or price is None
        ):
            continue
        unique_key = (stable_model, resolution, channel_id, source, raw_model)
        if unique_key in seen_routes:
            continue
        seen_routes.add(unique_key)
        internal_routes.append(
            {
                "stable_model": stable_model,
                "resolution": resolution,
                "channel_id": channel_id,
                "source": source,
                "raw_model": raw_model,
                "mapping_rule": mapping.get("rule_id") or mapping.get("match_type"),
                "price_version": price["version"],
                "trusted_cost": price["cost"],
            }
        )

    internal_routes.sort(
        key=lambda row: (
            row["stable_model"],
            row["resolution"],
            int(row.get("channel_id") or 0),
            row["source"],
            row["raw_model"],
        )
    )
    public_resolutions: dict[str, set[str]] = defaultdict(set)
    for route in internal_routes:
        public_resolutions[route["stable_model"]].add(route["resolution"])
    public_models = [
        {
            "id": model,
            "resolutions": sorted(
                resolutions,
                key=lambda value: (int(value.removesuffix("p")), value),
            ),
            "available": True,
        }
        for model, resolutions in sorted(public_resolutions.items())
    ]
    return {
        "internal": {
            "catalog_revision": checked["revision"],
            "routes": internal_routes,
        },
        "public": {
            "protocol": "xtai-relay-v1",
            "catalog_revision": checked["revision"],
            "models": public_models,
        },
    }
