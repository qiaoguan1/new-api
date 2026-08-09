#!/usr/bin/env python3
"""Collect mutable upstream video catalogs without exposing credentials."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import date, timedelta
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from video_catalog_policy import normalize_model_name, propose_model_candidate


REMOVED_SOURCES = {"packapi", "unity2"}
MAX_MODEL_NAME_LENGTH = 240
DEFAULT_TIMEOUT_SECONDS = 25


class CatalogCollectionError(RuntimeError):
    """Raised before any trusted snapshot is changed."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def extract_openai_model_names(payload: Any) -> list[str]:
    """Extract bounded unique IDs from an OpenAI-compatible model list."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    names = set()
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("id"))
        if name and len(name) <= MAX_MODEL_NAME_LENGTH:
            names.add(name)
    return sorted(names)


def read_enabled_channels(
    stack_root: str,
    *,
    runner: Any = subprocess.run,
    secrets: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Read enabled route credentials from PostgreSQL without printing them."""
    sql = (
        "SELECT COALESCE(json_agg(json_build_object("
        "'id',id,'name',name,'type',type,'base_url',base_url,'key',key,"
        "'status',status,'models',models) ORDER BY id),'[]'::json) "
        "FROM channels WHERE status=1;"
    )
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "newapi",
        "-d",
        "new-api",
        "-t",
        "-A",
        "-c",
        sql,
    ]
    try:
        completed = runner(
            command,
            cwd=str(stack_root),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        message = _redact(f"channel database query failed: {exc}", secrets)
        raise CatalogCollectionError(message) from exc
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout or "unknown psql failure"
        raise CatalogCollectionError(
            _redact(f"channel database query failed: {detail}", secrets)
        )
    try:
        rows = json.loads(completed.stdout.strip() or "[]")
    except (TypeError, ValueError) as exc:
        raise CatalogCollectionError("channel database returned invalid JSON") from exc
    if not isinstance(rows, list):
        raise CatalogCollectionError("channel database result must be a list")
    valid = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("id"), int)
        and row.get("status") == 1
    ]
    if len(valid) != len(rows):
        raise CatalogCollectionError("channel database returned malformed enabled-channel rows")
    return valid


def _hostname(value: Any) -> str:
    try:
        return (urlsplit(_text(value)).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def source_for_channel(channel: dict[str, Any], upstreams: Iterable[dict[str, Any]]) -> str | None:
    """Resolve a stable internal source slug from the registered host catalog."""
    searchable = " ".join(
        (_text(channel.get("name")), _text(channel.get("base_url")))
    ).casefold()
    if any(marker in searchable for marker in REMOVED_SOURCES):
        return None

    host = _hostname(channel.get("base_url"))
    alias_matches: list[str] = []
    for upstream in upstreams:
        if not isinstance(upstream, dict):
            continue
        slug = _text(upstream.get("slug"))
        if not slug or slug.casefold() in REMOVED_SOURCES:
            continue
        hosts = {
            _text(item).casefold().rstrip(".")
            for item in upstream.get("hosts") or []
            if _text(item)
        }
        if host and any(host == item or host.endswith("." + item) for item in hosts):
            return slug
        aliases = {
            _text(item).casefold()
            for item in upstream.get("aliases") or []
            if _text(item)
        }
        if any(alias in searchable for alias in aliases):
            alias_matches.append(slug)
    if len(set(alias_matches)) == 1:
        return alias_matches[0]
    channel_id = channel.get("id")
    return f"channel-{channel_id}" if isinstance(channel_id, int) and channel_id > 0 else None


def _redact(value: Any, secrets: Iterable[str] = ()) -> str:
    text = re.sub(r"\s+", " ", str(value or "unknown upstream error")).strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[a-z0-9._~+\-/=]+", r"\1[redacted]", text)
    text = re.sub(
        r"(?i)(token|key|secret|password)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[redacted]",
        text,
    )
    return text[:240]


def _models_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ValueError("upstream model collection requires an HTTPS base URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("upstream base URL must not contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    path = path + "/models" if path.casefold().endswith("/v1") else path + "/v1/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _channel_keys(channel: dict[str, Any]) -> list[str]:
    raw = channel.get("key")
    if not isinstance(raw, str):
        return []
    return list(dict.fromkeys(value.strip() for value in raw.splitlines() if value.strip()))


def _is_relevant_model(source: str, raw_name: str, policy: dict[str, Any]) -> bool:
    decision = normalize_model_name(source, raw_name, policy)
    if decision["status"] == "matched":
        return True
    lowered = raw_name.casefold()
    return (
        re.search(r"seedance[-_ ]*2[.\-_ ]*0(?:\D|$)", lowered) is not None
        or re.search(r"(?:^|[^a-z0-9])sd[-_ ]*2(?:\D|$)", lowered) is not None
    )


def fetch_channel_catalog(
    channel: dict[str, Any],
    source: str,
    policy: dict[str, Any],
    *,
    session: Any = None,
    collected_at: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Fetch one enabled channel catalog, trying its protected keys in order."""
    channel_id = channel.get("id")
    base_url = _text(channel.get("base_url"))
    keys = _channel_keys(channel)
    configured_models = sorted(
        {
            value.strip()
            for value in _text(channel.get("models")).split(",")
            if value.strip()
        }
    )
    base = {
        "channel_id": channel_id,
        "source": source,
        "complete": False,
        "collected_at": collected_at,
        "catalog_count": 0,
        "catalog_sha256": "",
        "relevant_models": [],
        "configured_models": configured_models,
    }
    if channel.get("status") != 1:
        return {**base, "error": "channel is not enabled"}
    if not base_url or not keys:
        return {**base, "error": "enabled channel lacks base URL or API key"}

    if session is None:
        import requests

        session = requests.Session()
    try:
        url = _models_url(base_url)
    except ValueError as exc:
        return {**base, "error": _redact(exc, keys)}
    last_error = "upstream model collection failed"
    for key in keys:
        try:
            response = session.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout,
                allow_redirects=False,
            )
            if response.status_code != 200:
                last_error = _redact(
                    f"models endpoint returned HTTP {response.status_code}: {response.text}",
                    keys,
                )
                continue
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                last_error = _redact(f"models endpoint returned invalid JSON: {exc}", keys)
                continue
            names = extract_openai_model_names(payload)
            if not names:
                last_error = "models endpoint returned no valid model IDs"
                continue
            relevant = sorted(name for name in names if _is_relevant_model(source, name, policy))
            digest = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
            return {
                **base,
                "complete": True,
                "catalog_count": len(names),
                "catalog_sha256": digest,
                "relevant_models": relevant,
            }
        except Exception as exc:  # requests and test doubles expose different exceptions
            last_error = _redact(f"models request failed: {exc}", keys)
    return {**base, "error": last_error}


def merge_complete_snapshot(
    previous: Any,
    observations: Iterable[dict[str, Any]],
    *,
    collected_at: str,
    policy_revision: str,
) -> dict[str, Any]:
    """Replace successful channels and retain prior rows for failed active ones."""
    previous_rows = previous.get("channels", []) if isinstance(previous, dict) else []
    previous_by_id = {
        row.get("channel_id"): row
        for row in previous_rows
        if isinstance(row, dict) and row.get("complete") is True
    }
    observed = [row for row in observations if isinstance(row, dict)]
    merged_rows = []
    failed_channels = []
    for observation in observed:
        channel_id = observation.get("channel_id")
        if observation.get("complete") is True:
            clean = {key: value for key, value in observation.items() if key != "error"}
            merged_rows.append(clean)
        else:
            failed_channels.append(channel_id)
            if channel_id in previous_by_id:
                merged_rows.append(previous_by_id[channel_id])
    merged_rows.sort(key=lambda row: (int(row.get("channel_id") or 0), _text(row.get("source"))))
    failed_channels = sorted(
        {item for item in failed_channels if isinstance(item, int)},
    )
    return {
        "snapshot": {
            "schema_version": 1,
            "policy_revision": policy_revision,
            "updated_at": collected_at,
            "channels": merged_rows,
        },
        "run": {
            "schema_version": 1,
            "policy_revision": policy_revision,
            "collected_at": collected_at,
            "complete_channels": sorted(
                int(row["channel_id"])
                for row in observed
                if row.get("complete") is True and isinstance(row.get("channel_id"), int)
            ),
            "failed_channels": failed_channels,
            "observations": observed,
        },
    }


def build_mapping_report(snapshot: Any, policy: dict[str, Any]) -> dict[str, Any]:
    """Normalize snapshot candidates while keeping each upstream route distinct."""
    matched = []
    review_required = []
    rows = snapshot.get("channels", []) if isinstance(snapshot, dict) else []
    for channel in rows:
        if not isinstance(channel, dict):
            continue
        source = _text(channel.get("source"))
        channel_id = channel.get("channel_id")
        for raw_model in channel.get("relevant_models") or []:
            if not isinstance(raw_model, str):
                continue
            decision = normalize_model_name(source, raw_model, policy)
            row = {
                **decision,
                "channel_id": channel_id,
                "source": source,
                "raw_model": raw_model,
            }
            if decision["status"] == "matched":
                matched.append(row)
            else:
                row["decision_status"] = decision["status"]
                suggestion = propose_model_candidate(source, raw_model, policy)
                if suggestion is not None:
                    row["suggested_mapping"] = suggestion
                review_required.append(row)
    sort_key = lambda row: (int(row.get("channel_id") or 0), row["source"], row["raw_model"])
    matched.sort(key=sort_key)
    review_required.sort(key=sort_key)
    return {
        "schema_version": 1,
        "policy_revision": policy["revision"],
        "matched": matched,
        "review_required": review_required,
    }


def build_route_gates(
    mapping_report: Any,
    daily_audit: Any,
    *,
    expected_day: str,
) -> list[dict[str, Any]]:
    """Keep only currently configured, enabled, and healthy raw routes."""
    if not isinstance(daily_audit, dict) or daily_audit.get("date") != expected_day:
        observed = daily_audit.get("date") if isinstance(daily_audit, dict) else None
        raise CatalogCollectionError(
            f"daily route audit is stale: expected {expected_day}, got {observed}"
        )
    channels = {
        row.get("channel_id"): row
        for row in daily_audit.get("channels") or []
        if isinstance(row, dict) and isinstance(row.get("channel_id"), int)
    }
    routes = []
    for mapping in mapping_report.get("matched", []) if isinstance(mapping_report, dict) else []:
        if not isinstance(mapping, dict):
            continue
        channel_id = mapping.get("channel_id")
        raw_model = _text(mapping.get("raw_model"))
        source = _text(mapping.get("source"))
        channel = channels.get(channel_id)
        if not channel or not raw_model or not source:
            continue
        configured = {
            value
            for value in channel.get("configured_models") or []
            if isinstance(value, str) and value
        }
        model_details = channel.get("models") or {}
        detail = model_details.get(raw_model) if isinstance(model_details, dict) else None
        availability = channel.get("availability") or {}
        healthy = (
            channel.get("status") == 1
            and channel.get("scan_status") == "ok"
            and isinstance(availability, dict)
            and availability.get("status") == "ok"
            and isinstance(detail, dict)
            and detail.get("available") is True
        )
        if raw_model not in configured or not healthy:
            continue
        routes.append(
            {
                "channel_id": channel_id,
                "source": source,
                "raw_model": raw_model,
                "enabled": True,
                "healthy": True,
            }
        )
    routes.sort(key=lambda row: (row["channel_id"], row["source"], row["raw_model"]))
    return routes


def _complete_actual_cost_row(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and row.get("actual_log_complete") is True
        and row.get("collection_status") == "complete"
        and row.get("last_attempt_status") == "complete"
    )


def build_trusted_price_evidence(
    ledger: Any,
    policy: dict[str, Any],
    *,
    target_day: str,
    lookback_days: int = 7,
) -> list[dict[str, Any]]:
    """Collect exact-route upstream costs for internal profit comparison only."""
    try:
        target = date.fromisoformat(target_day)
    except (TypeError, ValueError) as exc:
        raise CatalogCollectionError(f"invalid target day: {target_day}") from exc
    if not isinstance(lookback_days, int) or not 1 <= lookback_days <= 31:
        raise CatalogCollectionError("lookback_days must be between 1 and 31")
    days = ledger.get("days") if isinstance(ledger, dict) else None
    if not isinstance(days, dict):
        raise CatalogCollectionError("upstream balance ledger lacks days")

    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for offset in range(lookback_days):
        day = (target - timedelta(days=offset)).isoformat()
        accounts = days.get(day)
        if not isinstance(accounts, dict):
            continue
        same_day: dict[tuple[str, str], dict[str, Any]] = {}
        for source, account in accounts.items():
            if not isinstance(source, str) or not _complete_actual_cost_row(account):
                continue
            costs = account.get("per_model_real_cost") or {}
            if not isinstance(costs, dict):
                continue
            for raw_model, detail in costs.items():
                if not isinstance(raw_model, str) or not isinstance(detail, dict):
                    continue
                if detail.get("kind") != "video":
                    continue
                unit = detail.get("billing_unit")
                cost_key = {
                    "second": "cost_cny_per_second",
                    "call": "cost_cny_per_call",
                }.get(unit)
                cost = detail.get(cost_key) if cost_key else None
                if not _positive_number(cost):
                    continue
                decision = normalize_model_name(source, raw_model, policy)
                if decision["status"] != "matched":
                    continue
                key = (source, raw_model)
                candidate = {
                    "source": source,
                    "stable_model": decision["stable_model"],
                    "resolution": decision["resolution"],
                    "raw_model": raw_model,
                    "trusted": True,
                    "version": f"actual:{day}",
                    "evidence_type": "actual_deduction",
                    "sample_day": day,
                    "billing_unit": unit,
                    "unit_cost_cny": float(cost),
                }
                previous = same_day.get(key)
                if previous is None or candidate["unit_cost_cny"] > previous["unit_cost_cny"]:
                    same_day[key] = candidate
        for key, candidate in same_day.items():
            selected.setdefault(key, candidate)

    target_accounts = days.get(target_day)
    if isinstance(target_accounts, dict):
        for source, account in target_accounts.items():
            if not isinstance(source, str) or not isinstance(account, dict):
                continue
            metadata = account.get("pricing_metadata") or {}
            if metadata.get("status") != "complete":
                continue
            for row in metadata.get("models") or []:
                if not isinstance(row, dict) or row.get("billing_mode") != "per_call":
                    continue
                raw_model = _text(row.get("model_name"))
                cost = row.get("model_price")
                if not raw_model or not _positive_number(cost):
                    continue
                decision = normalize_model_name(source, raw_model, policy)
                if decision["status"] != "matched":
                    continue
                key = (source, raw_model)
                selected.setdefault(
                    key,
                    {
                        "source": source,
                        "stable_model": decision["stable_model"],
                        "resolution": decision["resolution"],
                        "raw_model": raw_model,
                        "trusted": True,
                        "version": f"catalog:{target_day}",
                        "evidence_type": "authenticated_catalog",
                        "sample_day": target_day,
                        "billing_unit": "call",
                        "unit_cost_cny": float(cost),
                    },
                )
    return sorted(
        selected.values(),
        key=lambda row: (row["source"], row["raw_model"]),
    )
