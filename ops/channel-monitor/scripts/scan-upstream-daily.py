#!/usr/bin/env python3
import copy
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from monitor_time import beijing_iso_now, resolve_beijing_business_day
from pathlib import Path
from urllib import error, parse, request

from pricing_audit_policy import actual_cost_alerts

from channel_audit_policy import (
    configured_model_pairs,
    intersect_pricing_catalog,
    parse_model_mapping,
    probe_body as policy_probe_body,
    probe_endpoint,
    select_metadata_probe_model,
    select_probe_model,
)

ROOT = Path(os.environ.get("CHANNEL_MONITOR_ROOT", "/opt/ai-api-stack/channel-monitor"))
UPSTREAMS_PATH = ROOT / "upstreams.json"
DAILY_AUDIT_PATH = ROOT / "data" / "daily-upstream-audit.json"
DAILY_COST_HISTORY_PATH = ROOT / "data" / "daily-cost-history.json"
PRICE_BASELINE_PATH = ROOT / "data" / "daily-price-baseline.json"
MONITOR_DATA_PATH = ROOT / "data" / "monitor-data.json"
BALANCE_LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"
POSTGRES_CONTAINER = os.environ.get("CHANNEL_MONITOR_POSTGRES", "ai-api-stack-postgres-1")
DB_USER = os.environ.get("CHANNEL_MONITOR_DB_USER", "newapi")
DB_NAME = os.environ.get("CHANNEL_MONITOR_DB_NAME", "new-api")
QUOTA_PER_USD = float(os.environ.get("CHANNEL_MONITOR_QUOTA_PER_USD", "500000"))
COST_DELTA_ALERT_RATIO = float(os.environ.get("UPSTREAM_COST_DELTA_ALERT_RATIO", "0.2"))
MIN_GROSS_MARGIN = float(os.environ.get("MIN_GROSS_MARGIN", "0.2"))
SELL_MARKUP_RATIO = float(os.environ.get("UPSTREAM_SELL_MARKUP_RATIO", "1.5"))
MIN_BASELINE_PRIORITY = int(os.environ.get("UPSTREAM_BASELINE_MIN_PRIORITY", "1"))

ENDPOINT_BY_TYPE = {
    1: "/v1/chat/completions",
    2: "/v1/responses",
    3: "/v1/chat/completions",
    8: "/v1/chat/completions",
    14: "/v1/chat/completions",
    15: "/v1/chat/completions",
    16: "/v1/chat/completions",
    17: "/v1/chat/completions",
    20: "/v1/chat/completions",
    24: "/v1/chat/completions",
    31: "/v1/chat/completions",
    35: "/v1/chat/completions",
    36: "/v1/chat/completions",
    40: "/v1/chat/completions",
    44: "/v1/chat/completions",
    45: "/v1/chat/completions",
    46: "/v1/chat/completions",
    47: "/v1/chat/completions",
    48: "/v1/chat/completions",
    49: "/v1/chat/completions",
}

GROUP_RE = re.compile(
    r"(?:under\s+group|group|分组)\s*[`'\"]?([^`'\"()（），。]*?(?:组|组.*|[A-Za-z][A-Za-z0-9_+ -]*|[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9_+ -]*?))[`'\"]?\s*(?:\s+(?:下模型|无可用渠道)|\(|（|，|。|$)",
    re.IGNORECASE,
)


def now_iso():
    return beijing_iso_now()


def target_beijing_day():
    """默认审计上一个完整北京时间日；可用 CHANNEL_MONITOR_DAY 覆盖。"""
    return resolve_beijing_business_day(os.environ.get("CHANNEL_MONITOR_DAY", ""))


def read_json(path, default):
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if value is not None else default
    except (OSError, json.JSONDecodeError):
        pass
    return copy.deepcopy(default)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_round(value, digits=6):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def run_psql(sql):
    cmd = [
        "docker",
        "exec",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-t",
        "-A",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def run_psql_json(sql):
    raw = run_psql(sql)
    return json.loads(raw) if raw else {}


def sql_quote(value):
    return "'" + str(value or "").replace("'", "''") + "'"


def host_of(url):
    try:
        return (parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def join_url(base, path):
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return (base or "").rstrip("/") + "/" + path.lstrip("/")


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Never forward channel credentials through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def redact_http_text(value, headers):
    """Remove authorization material before errors enter persisted audit data."""
    text = str(value or "")
    secrets = []
    for name, header_value in (headers or {}).items():
        if name.lower() not in {"authorization", "x-api-key"}:
            continue
        secret = str(header_value or "")
        if secret:
            secrets.append(secret)
            if secret.lower().startswith("bearer "):
                secrets.append(secret[7:])
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def http_json(url, method="GET", payload=None, headers=None, timeout=25):
    parsed_url = parse.urlsplit(url or "")
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        return None, None, "refusing non-HTTPS upstream request", 0
    data = None
    final_headers = {"User-Agent": "xingtu-upstream-monitor/1.0"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    final_headers.update(headers or {})
    req = request.Request(url, data=data, headers=final_headers, method=method)
    opener = request.build_opener(NoRedirectHandler())
    started = time.monotonic()
    status = None
    body = b""
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read(2_000_000)
    except error.HTTPError as exc:
        status = exc.code
        body = exc.read(2_000_000)
    except Exception as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return None, None, redact_http_text(exc, final_headers), elapsed_ms
    elapsed_ms = round((time.monotonic() - started) * 1000)
    decoded = redact_http_text(body.decode("utf-8", errors="replace"), final_headers)
    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, decoded[:1000], elapsed_ms


def fetch_channels():
    sql = r"""
SELECT jsonb_build_object(
    'channels', coalesce(jsonb_agg(jsonb_build_object(
        'id', c.id,
        'type', c.type,
        'key', c.key,
        'test_model', c.test_model,
        'status', c.status,
        'name', c.name,
        'base_url', c.base_url,
        'balance', c.balance,
        'balance_updated_time', c.balance_updated_time,
        'group', c."group",
        'models', c.models,
        'model_mapping', c.model_mapping,
        'priority', c.priority
    ) ORDER BY c.id), '[]'::jsonb),
    'model_ratio', (SELECT value FROM options WHERE key = 'ModelRatio'),
    'completion_ratio', (SELECT value FROM options WHERE key = 'CompletionRatio'),
    'group_ratio', (SELECT value FROM options WHERE key = 'GroupRatio'),
    'model_price', (SELECT value FROM options WHERE key = 'ModelPrice')
)::text
FROM channels c;
"""
    payload = run_psql_json(sql)
    payload["model_ratio"] = read_json_from_string(payload.get("model_ratio"), {})
    payload["completion_ratio"] = read_json_from_string(payload.get("completion_ratio"), {})
    payload["group_ratio"] = read_json_from_string(payload.get("group_ratio"), {})
    payload["model_price"] = read_json_from_string(payload.get("model_price"), {})
    return payload


def read_json_from_string(value, default):
    try:
        if not value:
            return copy.deepcopy(default)
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return copy.deepcopy(default)


def channel_matches_upstream(channel, upstream):
    name = (channel.get("name") or "").lower()
    base_host = host_of(channel.get("base_url"))
    hosts = [str(item).lower() for item in upstream.get("hosts", [])]
    aliases = [str(item).lower() for item in upstream.get("aliases", [])]
    if base_host and any(base_host == host or base_host.endswith("." + host) for host in hosts):
        return True
    return any(alias and alias in name for alias in aliases)


def extract_error_message(payload, body):
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "")[:500]
        for key in ("message", "msg", "error"):
            if payload.get(key):
                return str(payload.get(key))[:500]
    return (body or "")[:500]


def parse_upstream_group(message):
    text = message or ""
    patterns = [
        r"under\s+group\s+([^()（）]+?)(?:\s*\(|$)",
        r"分组\s+([^()（）]+?)\s+下模型",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip(" `'\"")
            if value and "nonexistent" not in value.lower():
                return value[:80]
    return ""


def endpoint_for_channel(channel):
    return ENDPOINT_BY_TYPE.get(safe_int(channel.get("type"), 1), "/v1/chat/completions")


def build_probe_body(model, endpoint):
    return policy_probe_body(model, endpoint)


def usage_cost(payload):
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and cost >= 0:
        return float(cost)
    details = usage.get("cost_details")
    if isinstance(details, dict):
        for key in ("upstream_inference_cost", "total_cost"):
            value = details.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return float(value)
    return None


def probe_channel(channel):
    base_url = (channel.get("base_url") or "").strip()
    key = (channel.get("key") or "").strip()
    model = select_probe_model(channel)
    result = {
        "test_model": model,
        "status": "skipped",
        "http_status": None,
        "latency_ms": None,
        "actual_cost_usd": None,
        "actual_cost_source": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "upstream_group": "",
        "error": "",
    }
    if not base_url or not key:
        result["error"] = "missing base_url or channel key"
        return result
    if not model:
        result["error"] = "channel has no configured probe model"
        return result

    endpoint = probe_endpoint(model, endpoint_for_channel(channel))
    probe_url = join_url(base_url, endpoint)
    bad_body = build_probe_body("__upstream_group_probe_nonexistent__", endpoint)
    status, payload, body, elapsed = http_json(
        probe_url,
        method="POST",
        payload=bad_body,
        headers={"Authorization": f"Bearer {key}"},
        timeout=25,
    )
    message = extract_error_message(payload, body)
    upstream_group = parse_upstream_group(message)
    result["upstream_group"] = upstream_group
    if upstream_group:
        result["group_probe_status"] = "ok"
    else:
        result["group_probe_status"] = "unknown"
        result["group_probe_error"] = message[:220]

    live_body = build_probe_body(model, endpoint)
    started = time.monotonic()
    status, payload, body, http_elapsed = http_json(
        probe_url,
        method="POST",
        payload=live_body,
        headers={"Authorization": f"Bearer {key}"},
        timeout=35,
    )
    elapsed = round((time.monotonic() - started) * 1000)
    result["http_status"] = status
    result["latency_ms"] = elapsed or http_elapsed
    cost = usage_cost(payload)
    if cost is not None:
        result["actual_cost_usd"] = cost
        result["actual_cost_source"] = "usage_cost"
    if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
        result["prompt_tokens"] = safe_int(payload["usage"].get("prompt_tokens"))
        result["completion_tokens"] = safe_int(payload["usage"].get("completion_tokens"))
    if status and 200 <= status < 300:
        result["status"] = "ok"
    else:
        result["status"] = "error"
        result["error"] = extract_error_message(payload, body)
    return result


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
    is_topaz = str(channel.get("type") or "") == "58"
    base_path = parse.urlsplit(base_url).path.rstrip("/")
    models_path = (
        "/video/status"
        if is_topaz
        else ("/models" if base_path.endswith("/v1") else "/v1/models")
    )
    headers = (
        {"X-API-Key": key}
        if is_topaz
        else {"Authorization": f"Bearer {key}"}
    )
    status, payload, body, elapsed = http_json(
        join_url(base_url, models_path),
        headers=headers,
        timeout=25,
    )
    result["http_status"] = status
    result["latency_ms"] = elapsed
    if is_topaz:
        rows = payload.get("supportedModels") if isinstance(payload, dict) else None
        advertised = {str(item).strip() for item in rows or [] if str(item).strip()}
        catalog_available = isinstance(payload, dict) and payload.get("isAvailable") is True
    else:
        rows = payload.get("data") if isinstance(payload, dict) else None
        advertised = {
            str(item.get("id") or item.get("model") or "").strip()
            for item in rows or []
            if isinstance(item, dict)
        }
        catalog_available = True
    configured = configured_model_pairs(channel)
    result["configured_model_count"] = len(configured)
    result["discovered_model_count"] = len(advertised)
    result["missing_models"] = [
        upstream_model
        for _, upstream_model in configured
        if upstream_model not in advertised
    ]
    if status != 200 or not isinstance(rows, list) or not catalog_available:
        result["error"] = (
            (payload.get("availabilityMessage") if isinstance(payload, dict) else "")
            or extract_error_message(payload, body)
            or f"HTTP {status}"
        )
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
    url = join_url(base_url, "/api/pricing")
    status, payload, body, elapsed = http_json(url, timeout=30)
    if status == 200 and isinstance(payload, dict) and payload.get("success") and isinstance(payload.get("data"), list):
        return {
            "status": "ok",
            "http_status": status,
            "latency_ms": elapsed,
            "pricing": payload,
            "error": "",
        }
    message = extract_error_message(payload, body) or f"HTTP {status}"
    return {
        "status": "unavailable" if status in (401, 403) else "error",
        "http_status": status,
        "latency_ms": elapsed,
        "pricing": None,
        "error": message,
    }


def parse_simple_tiered_prices(expr):
    tiers = re.findall(r'tier\s*\(\s*["\']([^"\']+)["\']\s*,\s*([^()]+?)\s*\)', expr or "")
    prices = []
    for tier_name, expression in tiers:
        p_match = re.search(r'(?<![A-Za-z0-9_])p\s*\*\s*([0-9]+(?:\.[0-9]+)?)', expression)
        c_match = re.search(r'(?<![A-Za-z0-9_])c\s*\*\s*([0-9]+(?:\.[0-9]+)?)', expression)
        if not p_match or not c_match:
            continue
        prices.append({
            "tier": tier_name,
            "input": safe_float(p_match.group(1), None),
            "output": safe_float(c_match.group(1), None),
        })
    return prices


def model_price(item, group):
    if not isinstance(item, dict):
        return None
    model_ratio = safe_float(item.get("model_ratio"), None)
    if model_ratio is None:
        return None
    completion_ratio = safe_float(item.get("completion_ratio"), 1.0)
    group_ratio = group.get("ratio", 1.0)
    billing_mode = item.get("billing_mode") or "ratio"
    billing_expr = item.get("billing_expr") or ""
    tier_prices = parse_simple_tiered_prices(billing_expr)
    if billing_mode == "tiered_expr" and tier_prices:
        base_input_usd = min(item["input"] for item in tier_prices) * group_ratio
        base_output_usd = min(item["output"] for item in tier_prices) * group_ratio
    else:
        base_input_usd = model_ratio * 2 * group_ratio
        base_output_usd = base_input_usd * completion_ratio
    return {
        "model_ratio": model_ratio,
        "completion_ratio": completion_ratio,
        "cache_ratio": safe_float(item.get("cache_ratio"), None),
        "create_cache_ratio": safe_float(item.get("create_cache_ratio"), None),
        "billing_mode": billing_mode,
        "billing_expr": billing_expr,
        "tier_prices": tier_prices,
        "base_input_usd_per_m": safe_round(base_input_usd),
        "base_output_usd_per_m": safe_round(base_output_usd),
    }


def scan_pricing(channel, upstream_group, ledger_entry=None):
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
    if not pricing:
        return {
            **fetched,
            "group": upstream_group,
            "group_ratio": None,
            "configured_models": [local for local, _ in configured_model_pairs(channel)],
            "unavailable_models": {},
            "models": {},
        }

    group_ratio_map = pricing.get("group_ratio") or {}
    group_ratio = safe_float(group_ratio_map.get(upstream_group), None) if upstream_group else None
    if group_ratio is None and len(group_ratio_map) == 1:
        upstream_group = next(iter(group_ratio_map.keys()))
        group_ratio = safe_float(group_ratio_map.get(upstream_group), None)
    if group_ratio is None:
        group_ratio = 1.0

    models = {}
    unavailable_models = {}
    account_models = (
        {
            str(model).strip()
            for model in metadata.get("account_models") or []
            if str(model).strip()
        }
        if metadata.get("status") == "complete"
        else None
    )
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
        group_ok = (
            upstream_model in account_models
            if account_models is not None
            else not enable_groups or not upstream_group or upstream_group in enable_groups
        )
        models[model_name] = {
            "available": group_ok,
            "upstream_model": upstream_model,
            "enable_groups": enable_groups,
            **(model_price(item, {"ratio": group_ratio}) or {}),
        }
    return {
        **fetched,
        "pricing_version": pricing.get("pricing_version", ""),
        "group": upstream_group,
        "group_ratio": group_ratio,
        "configured_models": [entry["local_model"] for entry in configured],
        "unavailable_models": unavailable_models,
        "models": models,
    }


def daily_cost_by_channel(channel_ids, day):
    if not channel_ids:
        return {}
    quoted_ids = ",".join(str(safe_int(channel_id)) for channel_id in channel_ids)
    sql = f"""
SELECT coalesce(jsonb_object_agg(channel_id, summary), '{{}}'::jsonb)::text
FROM (
    SELECT
        channel_id,
        jsonb_build_object(
            'calls', count(*),
            'success_calls', count(*) FILTER (WHERE type = 2),
            'error_calls', count(*) FILTER (WHERE type <> 2),
            'prompt_tokens', coalesce(sum(prompt_tokens), 0),
            'completion_tokens', coalesce(sum(completion_tokens), 0),
            'local_charge_quota', coalesce(sum(quota), 0),
            'upstream_cost_usd', coalesce(sum(nullif((other::jsonb ->> 'upstream_cost')::numeric, 0)), 0),
            'upstream_cost_samples', count(*) FILTER (WHERE nullif((other::jsonb ->> 'upstream_cost')::numeric, 0) IS NOT NULL)
        ) AS summary
    FROM logs
    WHERE channel_id IN ({quoted_ids})
      AND (to_timestamp(created_at) AT TIME ZONE 'Asia/Shanghai')::date = {sql_quote(day)}::date
    GROUP BY channel_id
) AS rows;
"""
    return run_psql_json(sql)


def load_balance_ledger(day):
    """读取余额/消费台账中某天的各上游真实成本，按 slug 索引。"""
    ledger = read_json(BALANCE_LEDGER_PATH, {})
    days = ledger.get("days") if isinstance(ledger, dict) else {}
    day_rows = (days or {}).get(day) or {}
    result = {}
    for slug, entry in day_rows.items():
        if not isinstance(entry, dict):
            continue
        result[slug] = entry
    return result


def is_image_model(model_name):
    m = (model_name or "").lower()
    return ("image" in m) or ("seedream" in m) or ("dall" in m) or ("flux" in m) or ("seedance" in m)


def channel_scoped_log_cost(ledger_entry, channel_models):
    """按渠道承载的模型，把台账"当天按模型的总成本(USD)"拆分求和。
    文渠道只算文模型成本，图渠道只算图模型成本，避免图成本被摊给文渠道（毛利误报）。
    channel_models: 渠道 models 列表（NewAPI channels.models 逗号拆分）。
    返回 None 表示无数据（回退到全账号合计）。"""
    per_model_usd = ledger_entry.get("per_model_cost_usd") or {}
    if not per_model_usd:
        return None
    if not channel_models:
        # 无渠道模型清单时退回全账号合计
        total = sum(safe_float(v, 0.0) for v in per_model_usd.values())
        return total if total > 0 else None
    model_set = {m.strip() for m in channel_models if m and m.strip()}
    scoped = 0.0
    matched = 0
    for model, cost in per_model_usd.items():
        if model in model_set:
            scoped += safe_float(cost, 0.0)
            matched += 1
    if matched == 0:
        return 0.0   # 渠道有 models 但今天这些模型在上游无消耗 → 成本0
    return scoped


def actual_daily_cost_source(day_summary, balance_row, local_quota_cost, ledger_entry=None, channel_models=None):
    ledger_entry = ledger_entry or {}
    # 最高优先级：上游消费日志，按渠道承载模型拆分（修图渠道毛利误报）
    scoped = channel_scoped_log_cost(ledger_entry, channel_models or [])
    if scoped is not None:
        return scoped, "upstream_log", 1.0

    upstream_cost = safe_float(day_summary.get("upstream_cost_usd"), 0.0)
    if upstream_cost > 0:
        return upstream_cost, "usage_cost", 1.0

    # 次优：余额差（含充值干扰，置信度略低）
    ledger_delta = safe_float(ledger_entry.get("day_balance_delta_usd"), None)
    if ledger_delta is not None:
        return ledger_delta, "balance_delta", 0.85

    balance_start = balance_row.get("start")
    balance_end = balance_row.get("end")
    if balance_start is not None and balance_end is not None:
        delta = safe_float(balance_start) - safe_float(balance_end)
        if delta > 0:
            return delta, "balance_delta", 0.9
        if delta == 0 and safe_int(day_summary.get("calls")) > 0:
            return 0.0, "balance_delta", 0.8

    calls = safe_int(day_summary.get("calls"))
    if calls > 0:
        return None, "actual_cost_unavailable", 0.0
    return 0.0, "no_traffic", 1.0


def gross_margin(revenue, cost):
    if revenue <= 0 or cost is None:
        return None
    return safe_round((revenue - cost) / revenue, 4)


def compare_previous(current, previous):
    changes = []
    previous_map = {item.get("channel_id"): item for item in previous.get("channels", [])}
    for channel in current.get("channels", []):
        old = previous_map.get(channel.get("channel_id")) or {}
        if channel.get("pricing_version") and old.get("pricing_version") and channel["pricing_version"] != old["pricing_version"]:
            changes.append({
                "type": "pricing_version_changed",
                "channel_id": channel.get("channel_id"),
                "channel_name": channel.get("name"),
                "from": old.get("pricing_version"),
                "to": channel.get("pricing_version"),
                "severity": "warning",
            })
        old_group = old.get("upstream_group")
        new_group = channel.get("upstream_group")
        if old_group and new_group and old_group != new_group:
            changes.append({
                "type": "upstream_group_changed",
                "channel_id": channel.get("channel_id"),
                "channel_name": channel.get("name"),
                "from": old_group,
                "to": new_group,
                "severity": "warning",
            })
        old_models = old.get("models") or {}
        for model, price in (channel.get("models") or {}).items():
            old_price = old_models.get(model) or {}
            if price.get("available") is False and old_price.get("available") is not False:
                changes.append({
                    "type": "model_unavailable",
                    "channel_id": channel.get("channel_id"),
                    "channel_name": channel.get("name"),
                    "model": model,
                    "severity": "critical",
                })
            old_input = safe_float(old_price.get("base_input_usd_per_m"), None)
            new_input = safe_float(price.get("base_input_usd_per_m"), None)
            if old_input and new_input and old_input > 0:
                ratio = (new_input - old_input) / old_input
                if abs(ratio) >= 0.05:
                    changes.append({
                        "type": "input_price_changed",
                        "channel_id": channel.get("channel_id"),
                        "channel_name": channel.get("name"),
                        "model": model,
                        "from": old_input,
                        "to": new_input,
                        "change_ratio": safe_round(ratio, 4),
                        "severity": "critical" if ratio > COST_DELTA_ALERT_RATIO else "warning",
                    })
    return changes


def local_sell_price(model, group, settings):
    model_price_map = settings.get("model_price") or {}
    if model in model_price_map and safe_float(model_price_map.get(model), 0) > 0:
        return safe_float(model_price_map.get(model)), safe_float(model_price_map.get(model))
    model_ratio = safe_float((settings.get("model_ratio") or {}).get(model), None)
    completion_ratio = safe_float((settings.get("completion_ratio") or {}).get(model), 1.0)
    group_ratio = safe_float((settings.get("group_ratio") or {}).get(group), 1.0)
    if model_ratio is None:
        return None, None
    input_price = model_ratio * 2 * group_ratio
    output_price = input_price * completion_ratio
    return safe_round(input_price), safe_round(output_price)


def build_price_baseline(channel_results, day):
    candidates = {}
    for row in channel_results:
        if row.get("status") != 1 or row.get("priority", 0) < MIN_BASELINE_PRIORITY:
            continue
        if row.get("pricing_status") != "ok":
            continue
        cost_source = (row.get("daily") or {}).get("actual_cost_source") or "no_traffic"
        cost_confidence = safe_float((row.get("daily") or {}).get("actual_cost_confidence"), 0.0)
        for model, price in (row.get("models") or {}).items():
            if price.get("available") is False:
                continue
            input_cost = safe_float(price.get("base_input_usd_per_m"), None)
            output_cost = safe_float(price.get("base_output_usd_per_m"), None)
            if input_cost is None or output_cost is None or input_cost <= 0 or output_cost <= 0:
                continue
            item = candidates.setdefault(model, [])
            item.append({
                "channel_id": row.get("channel_id"),
                "channel_name": row.get("name"),
                "upstream_slug": row.get("upstream_slug"),
                "upstream_group": row.get("upstream_group"),
                "priority": row.get("priority"),
                "input_cost_usd_per_m": safe_round(input_cost),
                "output_cost_usd_per_m": safe_round(output_cost),
                "pricing_status": row.get("pricing_status"),
                "pricing_version": row.get("pricing_version"),
                "actual_cost_source": cost_source,
                "actual_cost_confidence": cost_confidence,
            })

    models = {}
    for model, rows in sorted(candidates.items()):
        rows = sorted(rows, key=lambda item: (item["input_cost_usd_per_m"], item["output_cost_usd_per_m"]), reverse=True)
        base = rows[0]
        sell_input = base["input_cost_usd_per_m"] * SELL_MARKUP_RATIO
        sell_output = base["output_cost_usd_per_m"] * SELL_MARKUP_RATIO
        output_multiplier = 1.0
        if base["input_cost_usd_per_m"] > 0:
            output_multiplier = base["output_cost_usd_per_m"] / base["input_cost_usd_per_m"]
        models[model] = {
            "baseline_channel": base,
            "candidates": rows,
            "markup_ratio": SELL_MARKUP_RATIO,
            "sell_input_usd_per_m": safe_round(sell_input),
            "sell_output_usd_per_m": safe_round(sell_output),
            "recommended_standard_input_usd_per_m": safe_round(sell_input),
            "recommended_standard_output_usd_per_m": safe_round(sell_output),
            "recommended_model_ratio_at_group_1": safe_round(sell_input / 2),
            "recommended_completion_ratio": safe_round(output_multiplier),
        }
    return {
        "date": day,
        "generated_at": int(time.time()),
        "generated_at_iso": now_iso(),
        "markup_ratio": SELL_MARKUP_RATIO,
        "min_baseline_priority": MIN_BASELINE_PRIORITY,
        "models": models,
    }


def clean_previous_channels(previous):
    channels = previous.get("channels") if isinstance(previous, dict) else []
    if not isinstance(channels, list):
        return []
    for row in channels:
        group = row.get("upstream_group") or ""
        if "nonexistent" in group.lower():
            row["upstream_group"] = ""
    return channels


def base_channel_record(channel, upstream):
    """Build an audit row without parsing disabled-channel inventory."""
    status = safe_int(channel.get("status"))
    base = {
        "channel_id": safe_int(channel.get("id")),
        "name": channel.get("name"),
        "type": safe_int(channel.get("type")),
        "status": status,
        "base_url": channel.get("base_url"),
        "group": channel.get("group") or "",
        "priority": safe_int(channel.get("priority")),
        "upstream_slug": upstream.get("slug") or "",
        "test_model": "",
        "configured_models": [],
        "model_mapping": {},
    }
    if status == 1:
        base.update(
            {
                "test_model": select_probe_model(channel),
                "configured_models": [
                    local for local, _ in configured_model_pairs(channel)
                ],
                "model_mapping": parse_model_mapping(channel.get("model_mapping")),
            }
        )
    return base


def build_snapshot():
    started = time.time()
    day = target_beijing_day()
    upstreams = read_json(UPSTREAMS_PATH, [])
    settings = fetch_channels()
    channels = settings.get("channels", [])
    channel_map = {safe_int(item.get("id")): item for item in channels}
    channel_ids = list(channel_map.keys())
    cost_summary = daily_cost_by_channel(channel_ids, day)
    balance_ledger = load_balance_ledger(day)
    previous = read_json(DAILY_AUDIT_PATH, {"date": "", "channels": []})
    previous["channels"] = clean_previous_channels(previous)

    channel_results = []
    for channel in channels:
        channel_id = safe_int(channel.get("id"))
        upstream = next((item for item in upstreams if channel_matches_upstream(channel, item)), {})
        base = base_channel_record(channel, upstream)
        if safe_int(channel.get("status")) != 1:
            channel_results.append({
                **base,
                "scan_status": "skipped_disabled",
                "availability": None,
                "pricing_status": "skipped",
                "unavailable_models": {},
                "models": {},
            })
            continue

        ledger_entry = balance_ledger.get(upstream.get("slug") or "") or {}
        probe = metadata_probe_channel(channel, ledger_entry)
        pricing = scan_pricing(
            channel, probe.get("upstream_group") or "", ledger_entry
        )
        day_summary = cost_summary.get(str(channel_id)) or cost_summary.get(channel_id) or {}
        local_charge_quota = safe_float(day_summary.get("local_charge_quota"), 0.0)
        local_charge_usd = local_charge_quota / QUOTA_PER_USD
        channel_models = [upstream for _, upstream in configured_model_pairs(channel)]
        actual_cost, cost_source, cost_confidence = actual_daily_cost_source(
            day_summary,
            {},
            local_charge_usd,
            ledger_entry,
            channel_models,
        )
        revenue = local_charge_usd
        margin = gross_margin(revenue, actual_cost)
        channel_results.append({
            **base,
            "scan_status": "ok" if probe.get("status") == "ok" else "error",
            "availability": probe,
            "upstream_group": probe.get("upstream_group") or pricing.get("group") or "",
            "pricing_status": pricing.get("status"),
            "pricing_http_status": pricing.get("http_status"),
            "pricing_version": pricing.get("pricing_version", ""),
            "pricing_error": pricing.get("error", ""),
            "upstream_group_ratio": pricing.get("group_ratio"),
            "configured_models": pricing.get("configured_models", base["configured_models"]),
            "unavailable_models": pricing.get("unavailable_models", {}),
            "models": pricing.get("models", {}),
            "daily": {
                "date": day,
                **day_summary,
                "local_charge_usd": safe_round(local_charge_usd),
                "actual_cost_usd": safe_round(actual_cost),
                "actual_cost_source": cost_source,
                "actual_cost_confidence": cost_confidence,
                "gross_margin": margin,
            },
        })

    alerts = compare_previous({"channels": channel_results}, previous)
    for row in channel_results:
        if row.get("scan_status") == "error":
            alerts.append({
                "type": "availability_failed",
                "channel_id": row.get("channel_id"),
                "channel_name": row.get("name"),
                "error": (row.get("availability") or {}).get("error") or "probe failed",
                "severity": "critical",
            })
        if row.get("pricing_status") == "error":
            alerts.append({
                "type": "pricing_scan_failed",
                "channel_id": row.get("channel_id"),
                "channel_name": row.get("name"),
                "error": row.get("pricing_error"),
                "severity": "warning",
            })
        daily = row.get("daily") or {}
        if daily.get("actual_cost_source") == "actual_cost_unavailable" and safe_int(daily.get("calls")) > 0:
            alerts.append({
                "type": "actual_cost_unavailable",
                "channel_id": row.get("channel_id"),
                "channel_name": row.get("name"),
                "severity": "warning",
            })
        margin = daily.get("gross_margin")
        if margin is not None and margin < MIN_GROSS_MARGIN:
            alerts.append({
                "type": "low_margin_risk",
                "channel_id": row.get("channel_id"),
                "channel_name": row.get("name"),
                "gross_margin": margin,
                "actual_cost_usd": daily.get("actual_cost_usd"),
                "revenue_usd": daily.get("local_charge_usd"),
                "severity": "critical" if margin < 0 else "warning",
            })

        alerts.extend(
            actual_cost_alerts(
                row,
                balance_ledger.get(row.get("upstream_slug") or "") or {},
                local_sell_price,
                settings,
            )
        )

    price_baseline = build_price_baseline(channel_results, day)
    summary = {
        "channels": len(channel_results),
        "enabled_channels": sum(1 for row in channel_results if row.get("status") == 1),
        "ok_channels": sum(1 for row in channel_results if row.get("scan_status") == "ok"),
        "failed_channels": sum(1 for row in channel_results if row.get("scan_status") == "error"),
        "pricing_ok_channels": sum(1 for row in channel_results if row.get("pricing_status") == "ok"),
        "actual_cost_channels": sum(
            1
            for row in channel_results
            if (row.get("daily") or {}).get("actual_cost_source") in ("upstream_log", "usage_cost", "balance_delta", "billing")
        ),
        "baseline_models": len(price_baseline.get("models", {})),
        "alerts": len(alerts),
        "critical_alerts": sum(1 for alert in alerts if alert.get("severity") == "critical"),
        "duration_seconds": round(time.time() - started, 2),
    }

    snapshot = {
        "date": day,
        "generated_at": int(time.time()),
        "generated_at_iso": now_iso(),
        "summary": summary,
        "channels": channel_results,
        "alerts": alerts,
        "price_baseline": price_baseline,
    }

    history = read_json(DAILY_COST_HISTORY_PATH, [])
    if not isinstance(history, list):
        history = []
    compact = {
        "date": snapshot["date"],
        "generated_at": snapshot["generated_at"],
        "generated_at_iso": snapshot["generated_at_iso"],
        "summary": summary,
        "channels": [
            {
                "channel_id": row.get("channel_id"),
                "name": row.get("name"),
                "upstream_slug": row.get("upstream_slug"),
                "scan_status": row.get("scan_status"),
                "availability_latency_ms": (row.get("availability") or {}).get("latency_ms"),
                "upstream_group": row.get("upstream_group"),
                "pricing_status": row.get("pricing_status"),
                "pricing_version": row.get("pricing_version"),
                "daily": row.get("daily"),
            }
            for row in channel_results
        ],
        "alerts": alerts,
        "price_baseline_summary": {
            "markup_ratio": price_baseline.get("markup_ratio"),
            "models": len(price_baseline.get("models", {})),
        },
    }
    history = [item for item in history if item.get("date") != day]
    history.append(compact)
    history = sorted(history, key=lambda item: item.get("date", ""))[-90:]

    write_json(DAILY_AUDIT_PATH, snapshot)
    write_json(DAILY_COST_HISTORY_PATH, history)
    write_json(PRICE_BASELINE_PATH, price_baseline)
    inject_monitor_data(snapshot, history)
    return snapshot


def inject_monitor_data(snapshot, history):
    payload = read_json(MONITOR_DATA_PATH, None)
    if not isinstance(payload, dict):
        return
    payload["daily_upstream_audit"] = {
        "date": snapshot.get("date"),
        "generated_at": snapshot.get("generated_at"),
        "generated_at_iso": snapshot.get("generated_at_iso"),
        "summary": snapshot.get("summary"),
        "alerts": snapshot.get("alerts", []),
        "history": history[-30:],
        "price_baseline": snapshot.get("price_baseline"),
    }
    by_channel = {item.get("channel_id"): item for item in snapshot.get("channels", [])}
    for upstream in payload.get("upstreams", []):
        matched = []
        for channel in upstream.get("channels", []):
            audit = by_channel.get(safe_int(channel.get("id")))
            if audit:
                channel["daily_upstream_audit"] = audit
                matched.append(audit)
        if matched:
            upstream["daily_upstream_audit"] = {
                "ok_channels": sum(1 for row in matched if row.get("scan_status") == "ok"),
                "failed_channels": sum(1 for row in matched if row.get("scan_status") == "error"),
                "actual_cost_usd": safe_round(sum(safe_float((row.get("daily") or {}).get("actual_cost_usd"), 0.0) for row in matched)),
                "actual_cost_sources": sorted({(row.get("daily") or {}).get("actual_cost_source") or "unknown" for row in matched}),
            }
    write_json(MONITOR_DATA_PATH, payload)


if __name__ == "__main__":
    try:
        result = build_snapshot()
    except Exception as exc:
        print(f"daily upstream audit failed: {exc}", file=sys.stderr)
        raise
    print(
        "generated daily-upstream-audit.json: "
        f"{result['summary']['enabled_channels']} enabled channels, "
        f"{result['summary']['ok_channels']} ok, "
        f"{result['summary']['failed_channels']} failed, "
        f"{result['summary']['alerts']} alerts, "
        f"{result['summary']['actual_cost_channels']} actual-cost channels, "
        f"{result['summary']['baseline_models']} baseline models"
    )
