#!/usr/bin/env python3
"""Patch the production daily scan to prefer zero-cost authenticated metadata."""

import argparse
import ast
import os
from pathlib import Path


IMPORT_OLD = """    probe_endpoint,
    select_probe_model,
)"""
IMPORT_NEW = """    probe_endpoint,
    select_metadata_probe_model,
    select_probe_model,
)"""

METADATA_PROBE_OLD = """    return result


def fetch_pricing(base_url):
"""
METADATA_PROBE_NEW = """    return result


def metadata_probe_channel(channel, ledger_entry):
    metadata = (ledger_entry or {}).get("pricing_metadata") or {}
    metadata_complete = metadata.get("status") == "complete"
    base_url = (channel.get("base_url") or "").strip()
    key = (channel.get("key") or "").strip()
    result = {
        "test_model": "",
        "status": "error",
        "http_status": None,
        "latency_ms": None,
        "actual_cost_usd": None,
        "actual_cost_source": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "upstream_group": (ledger_entry or {}).get("group") or "",
        "group_probe_status": "authenticated_metadata",
        "availability_source": (
            "authenticated_model_pricing_metadata"
            if metadata_complete
            else "channel_model_catalog_metadata"
        ),
        "configured_model_count": 0,
        "discovered_model_count": 0,
        "account_model_count": len(metadata.get("account_models") or []),
        "priced_model_count": len(metadata.get("models") or []),
        "missing_models": [],
        "error": "",
    }
    if not base_url or not key:
        result["error"] = "missing base_url or channel key"
        return result
    base_path = parse.urlsplit(base_url).path.rstrip("/")
    models_path = "/models" if base_path.endswith("/v1") else "/v1/models"
    status, payload, body, elapsed = http_json(
        join_url(base_url, models_path),
        headers={"Authorization": f"Bearer {key}"},
        timeout=25,
    )
    result["http_status"] = status
    result["latency_ms"] = elapsed
    rows = payload.get("data") if isinstance(payload, dict) else None
    advertised = {
        str(item.get("id") or item.get("model") or "").strip()
        for item in rows or []
        if isinstance(item, dict)
    }
    configured = configured_model_pairs(channel)
    result["configured_model_count"] = len(configured)
    result["discovered_model_count"] = len(advertised)
    result["missing_models"] = [
        upstream_model
        for _, upstream_model in configured
        if upstream_model not in advertised
    ]
    if status != 200 or not isinstance(rows, list):
        result["error"] = extract_error_message(payload, body) or f"HTTP {status}"
        return result
    if metadata_complete:
        model = select_metadata_probe_model(
            channel,
            advertised,
            metadata.get("models") or [],
            result["upstream_group"],
            metadata.get("account_models"),
        )
    else:
        model = next(
            (
                upstream_model
                for _, upstream_model in configured
                if upstream_model in advertised
            ),
            "",
        )
    result["test_model"] = model
    if not model:
        result["error"] = (
            "no configured model is visible in the read-only upstream metadata"
        )
        return result
    result["status"] = "ok"
    return result


def fetch_pricing(base_url):
"""

PRICING_OLD = """def scan_pricing(channel, upstream_group):
    fetched = fetch_pricing(channel.get("base_url"))
    pricing = fetched.get("pricing")
"""
PRICING_NEW = """def scan_pricing(channel, upstream_group, ledger_entry=None):
    metadata = (ledger_entry or {}).get("pricing_metadata") or {}
    if metadata.get("status") == "complete":
        pricing = {
            "success": True,
            "pricing_version": metadata.get("pricing_version") or "",
            "group_ratio": metadata.get("group_ratio") or {},
            "data": metadata.get("models") or [],
        }
        fetched = {
            "status": "ok",
            "http_status": 200,
            "latency_ms": None,
            "pricing": pricing,
            "error": "",
            "source": "authenticated_account_metadata",
        }
    else:
        fetched = fetch_pricing(channel.get("base_url"))
        pricing = fetched.get("pricing")
"""

LOOP_OLD = """        probe = probe_channel(channel)
        pricing = scan_pricing(channel, probe.get("upstream_group") or "")
        day_summary = cost_summary.get(str(channel_id)) or cost_summary.get(channel_id) or {}
        local_charge_quota = safe_float(day_summary.get("local_charge_quota"), 0.0)
        local_charge_usd = local_charge_quota / QUOTA_PER_USD
        ledger_entry = balance_ledger.get(upstream.get("slug") or "") or {}
"""
LOOP_NEW = """        ledger_entry = balance_ledger.get(upstream.get("slug") or "") or {}
        probe = metadata_probe_channel(channel, ledger_entry)
        pricing = scan_pricing(
            channel, probe.get("upstream_group") or "", ledger_entry
        )
        day_summary = cost_summary.get(str(channel_id)) or cost_summary.get(channel_id) or {}
        local_charge_quota = safe_float(day_summary.get("local_charge_quota"), 0.0)
        local_charge_usd = local_charge_quota / QUOTA_PER_USD
"""

REPLACEMENTS = (
    (IMPORT_OLD, IMPORT_NEW, "metadata policy import"),
    (METADATA_PROBE_OLD, METADATA_PROBE_NEW, "metadata probe"),
    (PRICING_OLD, PRICING_NEW, "authenticated pricing fallback"),
    (LOOP_OLD, LOOP_NEW, "audit loop ordering"),
)


def _replace_once_or_verify(source, old, new, label):
    old_count = source.count(old)
    new_count = source.count(new)
    # Some replacement blocks deliberately retain the old anchor as a suffix.
    # A unique full replacement therefore proves the patch is already present.
    if new_count == 1:
        return source
    if old_count == 1 and new_count == 0:
        return source.replace(old, new, 1)
    raise RuntimeError(
        f"expected one legacy or patched {label}, found old={old_count}, new={new_count}"
    )


def transform(source):
    for old, new, label in REPLACEMENTS:
        source = _replace_once_or_verify(source, old, new, label)
    ast.parse(source)
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    updated = transform(source)
    if args.check and updated != source:
        raise RuntimeError("target still requires the metadata probe patch")
    if not args.check and updated != source:
        temporary = args.target.with_name(args.target.name + ".metadata-probe.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(
        "metadata probe patch verified: "
        f"mode={'check' if args.check else 'apply'}, changed={updated != source}"
    )


if __name__ == "__main__":
    main()
