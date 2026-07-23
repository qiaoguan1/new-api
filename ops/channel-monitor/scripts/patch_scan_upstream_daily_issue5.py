#!/usr/bin/env python3
"""Apply the reviewed Issue #5 integration to scan-upstream-daily.py."""

import argparse
import ast
import os
from pathlib import Path


IMPORT_OLD = "from urllib import error, parse, request\n"
IMPORT_NEW = """from urllib import error, parse, request

from channel_audit_policy import (
    configured_model_pairs,
    intersect_pricing_catalog,
    probe_body as policy_probe_body,
    probe_endpoint,
    select_probe_model,
)
"""

SQL_OLD = """        'models', c.models,
        'priority', c.priority
"""
SQL_NEW = """        'models', c.models,
        'model_mapping', c.model_mapping,
        'priority', c.priority
"""

BODY_OLD = """def build_probe_body(model, endpoint):
    if endpoint == "/v1/responses":
        return {
            "model": model,
            "input": "ping",
            "max_output_tokens": 1,
            "stream": False,
        }
    return {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
"""
BODY_NEW = """def build_probe_body(model, endpoint):
    return policy_probe_body(model, endpoint)
"""

PROBE_MODEL_OLD = """    model = (channel.get("test_model") or "").strip() or "gpt-5.5"
    result = {
"""
PROBE_MODEL_NEW = """    model = select_probe_model(channel)
    result = {
        "test_model": model,
"""

PROBE_MISSING_OLD = """    if not base_url or not key:
        result["error"] = "missing base_url or channel key"
        return result

    endpoint = endpoint_for_channel(channel)
"""
PROBE_MISSING_NEW = """    if not base_url or not key:
        result["error"] = "missing base_url or channel key"
        return result
    if not model:
        result["error"] = "channel has no configured probe model"
        return result

    endpoint = probe_endpoint(model, endpoint_for_channel(channel))
"""

PRICING_EMPTY_OLD = """            "group_ratio": None,
            "models": {},
        }
"""
PRICING_EMPTY_NEW = """            "group_ratio": None,
            "configured_models": [local for local, _ in configured_model_pairs(channel)],
            "unavailable_models": {},
            "models": {},
        }
"""

PRICING_LOOP_OLD = """    models = {}
    supported = {
        str(model).strip()
        for model in (channel.get("models") or "").split(",")
        if str(model).strip()
    }
    for item in pricing.get("data", []):
        model_name = item.get("model_name")
        if model_name not in supported:
            continue
        enable_groups = item.get("enable_groups") or []
        group_ok = True
        if enable_groups and upstream_group:
            group_ok = upstream_group in enable_groups
        models[model_name] = {
            "available": group_ok,
            "enable_groups": enable_groups,
            **(model_price(item, {"ratio": group_ratio}) or {}),
        }
"""
PRICING_LOOP_NEW = """    models = {}
    unavailable_models = {}
    configured = intersect_pricing_catalog(channel, pricing.get("data", []))
    for entry in configured:
        model_name = entry["local_model"]
        upstream_model = entry["upstream_model"]
        item = entry["pricing"]
        if not item:
            unavailable_models[model_name] = {
                "upstream_model": upstream_model,
                "reason": "not_in_upstream_pricing_catalog",
            }
            continue
        enable_groups = item.get("enable_groups") or []
        group_ok = True
        if enable_groups and upstream_group:
            group_ok = upstream_group in enable_groups
        models[model_name] = {
            "available": group_ok,
            "upstream_model": upstream_model,
            "enable_groups": enable_groups,
            **(model_price(item, {"ratio": group_ratio}) or {}),
        }
"""

PRICING_RETURN_OLD = """        "group": upstream_group,
        "group_ratio": group_ratio,
        "models": models,
    }
"""
PRICING_RETURN_NEW = """        "group": upstream_group,
        "group_ratio": group_ratio,
        "configured_models": [entry["local_model"] for entry in configured],
        "unavailable_models": unavailable_models,
        "models": models,
    }
"""

BASE_OLD = """            "upstream_slug": upstream.get("slug") or "",
            "test_model": channel.get("test_model") or "gpt-5.5",
        }
"""
BASE_NEW = """            "upstream_slug": upstream.get("slug") or "",
            "test_model": select_probe_model(channel),
            "configured_models": [local for local, _ in configured_model_pairs(channel)],
        }
"""

DISABLED_OLD = """                "pricing_status": "skipped",
                "models": {},
            })
"""
DISABLED_NEW = """                "pricing_status": "skipped",
                "unavailable_models": {},
                "models": {},
            })
"""

CHANNEL_MODELS_OLD = """        channel_models = [m.strip() for m in (channel.get("models") or "").split(",") if m.strip()]
"""
CHANNEL_MODELS_NEW = """        channel_models = [upstream for _, upstream in configured_model_pairs(channel)]
"""

RESULT_OLD = """            "upstream_group_ratio": pricing.get("group_ratio"),
            "models": pricing.get("models", {}),
            "daily": {
"""
RESULT_NEW = """            "upstream_group_ratio": pricing.get("group_ratio"),
            "configured_models": pricing.get("configured_models", base["configured_models"]),
            "unavailable_models": pricing.get("unavailable_models", {}),
            "models": pricing.get("models", {}),
            "daily": {
"""


REPLACEMENTS = (
    (IMPORT_OLD, IMPORT_NEW, "policy import"),
    (SQL_OLD, SQL_NEW, "model_mapping query"),
    (BODY_OLD, BODY_NEW, "probe body"),
    (PROBE_MODEL_OLD, PROBE_MODEL_NEW, "probe model selection"),
    (PROBE_MISSING_OLD, PROBE_MISSING_NEW, "probe endpoint selection"),
    (PRICING_EMPTY_OLD, PRICING_EMPTY_NEW, "unavailable pricing inventory"),
    (PRICING_LOOP_OLD, PRICING_LOOP_NEW, "catalog intersection"),
    (PRICING_RETURN_OLD, PRICING_RETURN_NEW, "pricing inventory result"),
    (BASE_OLD, BASE_NEW, "audit base inventory"),
    (DISABLED_OLD, DISABLED_NEW, "disabled audit shape"),
    (CHANNEL_MODELS_OLD, CHANNEL_MODELS_NEW, "mapped ledger scope"),
    (RESULT_OLD, RESULT_NEW, "audit result inventory"),
)


def transform(source):
    for old, new, label in REPLACEMENTS:
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"expected one {label} block, found {count}")
        source = source.replace(old, new, 1)
    ast.parse(source)
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    updated = transform(source)
    if not args.check:
        temporary = args.target.with_name(args.target.name + ".issue5.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(f"issue5 scan patch verified: replacements={len(REPLACEMENTS)} write={not args.check}")


if __name__ == "__main__":
    main()
