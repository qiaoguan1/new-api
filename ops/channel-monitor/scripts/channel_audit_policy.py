#!/usr/bin/env python3
"""Pure policy helpers for channel audit inventory and probe selection."""

import json


IMAGE_MODEL_MARKERS = ("image", "seedream", "dall", "flux", "banana")


def parse_models(value):
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        values = []
    result = []
    seen = set()
    for value in values:
        model = str(value).strip()
        if model and model not in seen:
            seen.add(model)
            result.append(model)
    return result


def parse_model_mapping(value):
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except json.JSONDecodeError:
            value = {}
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip(): str(target).strip()
        for source, target in value.items()
        if str(source).strip() and str(target).strip()
    }


def configured_model_pairs(channel):
    """Return ordered (local model, upstream model) pairs for one channel."""
    mapping = parse_model_mapping(channel.get("model_mapping"))
    return [(model, mapping.get(model, model)) for model in parse_models(channel.get("models"))]


def is_image_model(model):
    name = str(model or "").lower()
    return any(marker in name for marker in IMAGE_MODEL_MARKERS)


def select_probe_model(channel):
    """Select a configured model; never invent the legacy gpt-5.5 fallback."""
    pairs = configured_model_pairs(channel)
    local_to_upstream = dict(pairs)
    upstream_models = {upstream for _, upstream in pairs}
    explicit = str(channel.get("test_model") or "").strip()
    if explicit in local_to_upstream:
        return local_to_upstream[explicit]
    if explicit in upstream_models:
        return explicit
    if not pairs:
        return ""
    group_hint = " ".join(
        str(channel.get(key) or "").lower() for key in ("name", "group")
    )
    image_channel = any(marker in group_hint for marker in ("图", "image", "生图"))
    if image_channel:
        for local, upstream in pairs:
            if is_image_model(local) or is_image_model(upstream):
                return upstream
    return pairs[0][1]


def select_metadata_probe_model(
    channel, advertised_models, pricing_rows, upstream_group="", account_models=None
):
    """Choose a configured model proven by all read-only metadata sources."""
    advertised = {
        str(model).strip() for model in advertised_models or [] if str(model).strip()
    }
    catalog = {
        str(row.get("model_name") or "").strip(): row
        for row in pricing_rows or []
        if isinstance(row, dict) and str(row.get("model_name") or "").strip()
    }
    account_catalog = None
    if account_models is not None:
        account_catalog = {
            str(model).strip() for model in account_models if str(model).strip()
        }
    for _, upstream_model in configured_model_pairs(channel):
        row = catalog.get(upstream_model)
        if (
            upstream_model not in advertised
            or not row
            or (account_catalog is not None and upstream_model not in account_catalog)
        ):
            continue
        enable_groups = parse_models(row.get("enable_groups"))
        # `/api/user/models` is already filtered for the authenticated account.
        # Its visibility is more authoritative than an upstream's internal
        # pricing-group labels, which may use a separate naming scheme.
        if (
            account_catalog is None
            and enable_groups
            and upstream_group
            and upstream_group not in enable_groups
        ):
            continue
        return upstream_model
    return ""


def probe_endpoint(model, default_endpoint="/v1/chat/completions"):
    if is_image_model(model):
        return "/v1/images/generations"
    return default_endpoint


def probe_body(model, endpoint):
    if endpoint == "/v1/images/generations":
        return {"model": model, "prompt": "a small white square", "n": 1}
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


def intersect_pricing_catalog(channel, pricing_rows):
    """Index upstream pricing by configured local names, honoring model_mapping."""
    catalog = {
        str(item.get("model_name") or "").strip(): item
        for item in pricing_rows or []
        if isinstance(item, dict) and str(item.get("model_name") or "").strip()
    }
    result = []
    for local_model, upstream_model in configured_model_pairs(channel):
        result.append(
            {
                "local_model": local_model,
                "upstream_model": upstream_model,
                "pricing": catalog.get(upstream_model),
            }
        )
    return result
