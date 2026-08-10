#!/usr/bin/env python3
"""Collect the previous Beijing day's actual upstream account deductions.

Supports classic NewAPI accounts and the newer /api/v1 auth/usage API. Every
credential receives a dated ledger row. A cost of zero is only written after a
complete log query; failures are written as incomplete with null cost.
"""

import base64
import json
import math
import os
import pathlib
import re
import stat
import sys
import time
from urllib.parse import urlsplit

import requests

from monitor_time import (
    beijing_day_for_epoch,
    beijing_iso_now,
    resolve_beijing_business_day,
)
from video_consumption import (
    dedupe_provider_usage,
    parse_newapi_video_task_rows,
    parse_toonflow_operation_rows,
)


ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAMS_PATH = ROOT / "upstreams.json"
CRED_PATH = ROOT / "upstream-credentials.json"
LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"
TOONFLOW_TOKEN_PATH = pathlib.Path(
    os.environ.get(
        "CHANNEL_MONITOR_TOONFLOW_TOKEN_FILE",
        str(ROOT / "secrets" / "toonflow-web-token"),
    )
)

TIMEOUT = 25
PAGE_SIZE = 100
MAX_PAGES = 100
QUOTA_PER_USD = 500000.0
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
PRICING_MODEL_FIELDS = (
    "model_name",
    "model_ratio",
    "completion_ratio",
    "cache_ratio",
    "create_cache_ratio",
    "billing_mode",
    "billing_expr",
    "model_price",
    "quota_type",
    "enable_groups",
)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json(path, value):
    temporary = pathlib.Path(str(path) + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, path)


def target_beijing_day():
    return resolve_beijing_business_day(os.environ.get("CHANNEL_MONITOR_DAY", ""))


def safe_float(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _positive_sample_price(value):
    number = safe_float(value)
    return number is not None and number > 0


def q2usd(quota):
    number = safe_float(quota)
    return round(number / QUOTA_PER_USD, 6) if number is not None else None


def origin_of(url):
    parsed = urlsplit(url or "")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def clean_error(value, secrets=()):
    text = re.sub(r"\s+", " ", str(value or "unknown error")).strip()
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[redacted]")
    return text[:240]


def json_response(response, label):
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{label} returned non-json (http {response.status_code})") from exc


def standard_login(session, origin, username, password):
    response = session.post(
        origin + "/api/user/login",
        json={"username": username, "password": password},
        timeout=TIMEOUT,
    )
    body = json_response(response, "classic login")
    if response.status_code != 200 or not body.get("success"):
        raise RuntimeError(
            f"classic login failed (http {response.status_code}): {clean_error(body.get('message'))}"
        )
    data = body.get("data") or {}
    # Support both legacy direct-user login responses and newer token+nested-user responses.
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    access_token = data.get("access_token")
    if access_token:
        session.headers.update({"Authorization": f"Bearer {access_token}"})
    user_id = user.get("id")
    if user_id is None:
        raise RuntimeError("classic login succeeded without user id")
    session.headers.update({"New-Api-User": str(user_id)})
    return user.get("group") or ""


def standard_self(session, origin):
    response = session.get(origin + "/api/user/self", timeout=TIMEOUT)
    body = json_response(response, "classic self")
    if response.status_code != 200 or not body.get("success"):
        raise RuntimeError(f"classic self failed: {clean_error(body.get('message'))}")
    return body.get("data") or {}


def standard_pricing_metadata(session, origin):
    """Fetch and sanitize account-authenticated, read-only pricing metadata."""
    response = session.get(origin + "/api/pricing", timeout=TIMEOUT)
    body = json_response(response, "classic pricing")
    rows = body.get("data")
    if response.status_code != 200 or not body.get("success") or not isinstance(rows, list):
        raise RuntimeError(
            f"classic pricing failed (http {response.status_code}): "
            f"{clean_error(body.get('message'))}"
        )
    models = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("model_name") or "").strip():
            continue
        models.append({key: row.get(key) for key in PRICING_MODEL_FIELDS if key in row})
    response = session.get(origin + "/api/user/models", timeout=TIMEOUT)
    body_models = json_response(response, "classic account models")
    raw_account_models = body_models.get("data")
    if (
        response.status_code != 200
        or not body_models.get("success")
        or not isinstance(raw_account_models, list)
    ):
        raise RuntimeError(
            f"classic account models failed (http {response.status_code}): "
            f"{clean_error(body_models.get('message'))}"
        )
    account_models = sorted(
        {
            str(item.get("id") or item.get("model") or "").strip()
            if isinstance(item, dict)
            else str(item).strip()
            for item in raw_account_models
        }
        - {""}
    )
    group_ratio = body.get("group_ratio")
    return {
        "status": "complete",
        "pricing_version": str(body.get("pricing_version") or ""),
        "group_ratio": group_ratio if isinstance(group_ratio, dict) else {},
        "models": models,
        "account_models": account_models,
        "fetched_at": int(time.time()),
    }


def standard_logs(session, origin, day):
    total_quota = 0.0
    rows = 0
    per_model = {}
    details = {}
    reached_older_day = False
    completed = False
    for page in range(1, MAX_PAGES + 1):
        response = session.get(
            origin + "/api/log/self",
            params={"p": page, "page_size": PAGE_SIZE},
            timeout=TIMEOUT,
        )
        body = json_response(response, f"classic log page {page}")
        if response.status_code != 200 or not body.get("success"):
            raise RuntimeError(f"classic log page {page} failed: {clean_error(body.get('message'))}")
        items = ((body.get("data") or {}).get("items")) or []
        if not items:
            completed = True
            break
        for item in items:
            try:
                item_day = beijing_day_for_epoch(item.get("created_at"))
            except (TypeError, ValueError, OSError):
                continue
            if item_day < day:
                reached_older_day = True
                continue
            if item_day != day or item.get("type") != 2:
                continue
            quota = safe_float(item.get("quota"), 0.0) or 0.0
            prompt = int(item.get("prompt_tokens") or 0)
            completion = int(item.get("completion_tokens") or 0)
            model = item.get("model_name") or "unknown"
            rows += 1
            total_quota += quota
            per_model[model] = per_model.get(model, 0.0) + quota
            detail = details.setdefault(
                model,
                {
                    "calls": 0,
                    "sum_quota": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "pricing_samples": [],
                },
            )
            detail["calls"] += 1
            detail["sum_quota"] += quota
            detail["prompt_tokens"] += prompt
            detail["completion_tokens"] += completion
            try:
                other = json.loads(item.get("other") or "{}")
            except (TypeError, ValueError):
                other = {}
            detail["pricing_samples"].append(
                {
                    "billing_mode": other.get("billing_mode") or "ratio",
                    "model_ratio": safe_float(other.get("model_ratio"), 0.0),
                    "completion_ratio": safe_float(other.get("completion_ratio"), 0.0),
                    "group_ratio": safe_float(other.get("group_ratio"), 0.0),
                    "expr_b64": other.get("expr_b64") or "",
                    "matched_tier": other.get("matched_tier") or "",
                    "billing_type": other.get("billing_type") or "",
                    "billing_source": other.get("billing_source") or "",
                    "duration": safe_float(other.get("duration"), 0.0),
                    "model_price": safe_float(other.get("model_price"), 0.0),
                    "price_per_call": safe_float(other.get("price_per_call"), 0.0),
                    "price_per_sec": safe_float(other.get("price_per_sec"), 0.0),
                    "quota": quota,
                }
            )
        if reached_older_day or len(items) < PAGE_SIZE:
            completed = True
            break
    if not completed:
        raise RuntimeError(f"classic logs exceed safety limit ({MAX_PAGES * PAGE_SIZE} rows)")
    return rows, total_quota, per_model, details


def standard_video_tasks(session, origin, day, provider_id, rate):
    """Fetch a complete authenticated NewAPI video-task set for one Beijing day."""
    result = []
    completed = False
    for page in range(1, MAX_PAGES + 1):
        response = session.get(
            origin + "/api/task/self",
            params={"p": page, "page_size": PAGE_SIZE},
            timeout=TIMEOUT,
        )
        body = json_response(response, f"video task page {page}")
        data = body.get("data") if isinstance(body, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if response.status_code != 200 or not body.get("success") or not isinstance(items, list):
            raise RuntimeError(
                f"video task page {page} failed (http {response.status_code}): "
                f"{clean_error(body.get('message'))}"
            )
        result.extend(
            parse_newapi_video_task_rows(
                body,
                day,
                provider_id=provider_id,
                rate=rate,
                quota_per_usd=QUOTA_PER_USD,
            )
        )
        total = int(data.get("total") or 0)
        if total:
            if page * PAGE_SIZE >= total:
                completed = True
                break
            if len(items) < PAGE_SIZE:
                raise RuntimeError(
                    f"video task pagination incomplete: expected {total}, received short page {page}"
                )
        elif not items or len(items) < PAGE_SIZE:
            completed = True
            break
    if not completed:
        raise RuntimeError(f"video tasks exceed safety limit ({MAX_PAGES * PAGE_SIZE} rows)")
    return dedupe_provider_usage(result)


def validate_video_task_evidence(rows, expected_cost_cny):
    """Fail closed when task-level costs do not reconcile to the complete billing ledger."""
    evidence = [dict(row) for row in rows or []]
    expected = safe_float(expected_cost_cny)
    if expected is None or expected < 0:
        status = "billing_total_unavailable"
    else:
        actual = sum(
            float(row.get("actual_cost_cny") or 0)
            for row in evidence
            if row.get("actual_cost_status") in {"actual", "zero_verified"}
        )
        tolerance = max(0.01, abs(expected) * 0.005)
        status = "complete" if abs(actual - expected) <= tolerance else "cost_mismatch"
    if status != "complete":
        for row in evidence:
            row["actual_cost_cny"] = None
            row["actual_cost_status"] = "unknown"
    return evidence, status


def _toonflow_json_get(session, url, *, params=None, label):
    response = session.get(
        url,
        params=params or {},
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    raw_content = getattr(response, "content", b"")
    if isinstance(raw_content, (bytes, bytearray)) and len(raw_content) > 4 * 1024 * 1024:
        raise RuntimeError(f"{label} response exceeds 4 MiB safety limit")
    body = json_response(response, label)
    mapping = body if isinstance(body, dict) else {}
    code = mapping.get("code")
    if (
        response.status_code != 200
        or not isinstance(body, dict)
        or mapping.get("success") is False
        or (code is not None and str(code) not in {"0", "200"})
    ):
        raise RuntimeError(
            f"{label} failed (http {response.status_code}): "
            f"{clean_error(mapping.get('message') or mapping.get('msg'))}"
        )
    return body


def _toonflow_token(credential, token_path=None):
    token = str(credential.get("web_token") or credential.get("access_token") or "").strip()
    path = pathlib.Path(token_path or TOONFLOW_TOKEN_PATH)
    if not token:
        try:
            token_stat = path.stat()
            if token_stat.st_size > 16_384:
                raise RuntimeError("Toonflow web token file is unexpectedly large")
            if os.name == "posix" and stat.S_IMODE(token_stat.st_mode) & 0o077:
                raise RuntimeError("Toonflow web token file permissions must be 0600")
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            token = ""
        except OSError as exc:
            raise RuntimeError("Toonflow web token file cannot be read") from exc
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise RuntimeError("missing operator-authorized Toonflow web token (CAPTCHA login required)")
    if len(token) > 16_384 or any(character.isspace() for character in token):
        raise RuntimeError("operator-authorized Toonflow web token is malformed")
    parts = token.split(".")
    if len(parts) == 3:
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            expires = int(claims.get("exp") or 0)
        except Exception:
            expires = 0
        if expires and expires <= int(time.time()) + 60:
            raise RuntimeError("operator-authorized Toonflow web token is expired")
    return token


def toonflow_operation_logs(session, origin, token, day, rate):
    """Fetch a complete Toonflow operation-log set using an authorized Bearer token."""
    session.headers.update({"Authorization": f"Bearer {token}"})
    result = []
    completed = False
    for page in range(1, MAX_PAGES + 1):
        body = _toonflow_json_get(
            session,
            origin + "/web/web/operationLog/getOperationLog",
            params={"page": page, "limit": PAGE_SIZE, "taskICode": ""},
            label=f"Toonflow operation log page {page}",
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        items = None
        for key in ("data", "list", "rows", "records", "items"):
            if isinstance(data.get(key), list):
                items = data.get(key)
                break
        if items is None:
            raise RuntimeError(f"Toonflow operation log page {page} has no record list")
        result.extend(parse_toonflow_operation_rows(body, day, rate=rate))
        total = int(data.get("total") or data.get("count") or 0)
        if total:
            if page * PAGE_SIZE >= total:
                completed = True
                break
            if len(items) < PAGE_SIZE:
                raise RuntimeError(
                    f"Toonflow pagination incomplete: expected {total}, received short page {page}"
                )
        elif not items or len(items) < PAGE_SIZE:
            completed = True
            break
    if not completed:
        raise RuntimeError(f"Toonflow logs exceed safety limit ({MAX_PAGES * PAGE_SIZE} rows)")
    return dedupe_provider_usage(result)


def _toonflow_pricing_metadata(session, origin):
    body = _toonflow_json_get(
        session,
        origin + "/web/web/model/getModelData",
        label="Toonflow model catalog",
    )
    data = body.get("data")
    candidates = data if isinstance(data, list) else []
    if isinstance(data, dict):
        for key in ("list", "rows", "records", "items"):
            if isinstance(data.get(key), list):
                candidates = data.get(key)
                break
    allowed = ("modelName", "name", "ability", "quality", "price", "billingType")
    models = [
        {key: row.get(key) for key in allowed if key in row}
        for row in candidates
        if isinstance(row, dict)
    ]
    return {
        "status": "complete",
        "models": models,
        "account_models": sorted(
            {
                str(row.get("modelName") or row.get("name") or "").strip()
                for row in candidates
                if isinstance(row, dict)
            }
            - {""}
        ),
        "fetched_at": int(time.time()),
    }


def collect_toonflow(credential, website, ledger, day):
    origin = origin_of(credential.get("website_url") or website)
    if urlsplit(origin).scheme != "https":
        raise RuntimeError("refusing Toonflow authentication over non-HTTPS transport")
    parsed_origin = urlsplit(origin)
    if (parsed_origin.hostname or "").lower() != "api.toonflow.net" or parsed_origin.port not in (
        None,
        443,
    ):
        raise RuntimeError("refusing to send Toonflow web token to an unapproved host")
    token = _toonflow_token(credential)
    rate = safe_float(credential.get("rate"), 1.0) or 1.0
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": origin,
            "Referer": origin + "/",
        }
    )
    evidence = toonflow_operation_logs(session, origin, token, day, rate)
    raw_cost = sum(float(row.get("actual_cost_cny") or 0) for row in evidence) / rate
    per_model = {}
    model_real = {}
    for row in evidence:
        model = row.get("raw_model") or row.get("stable_model") or "unknown"
        actual = float(row.get("actual_cost_cny") or 0)
        per_model[model] = per_model.get(model, 0.0) + actual / rate
        detail = model_real.setdefault(
            model,
            {
                "kind": "video_actual",
                "billing_unit": "task",
                "successful_calls": 0,
                "net_cost_cny": 0.0,
                "pricing_source": "toonflow_web_operation_log",
            },
        )
        if row.get("state") == "completed":
            detail["successful_calls"] += 1
        detail["net_cost_cny"] += actual
    for detail in model_real.values():
        detail["net_cost_cny"] = round(detail["net_cost_cny"], 8)
    balance = None
    used = None
    try:
        dashboard = _toonflow_json_get(
            session,
            origin + "/web/web/pointsPreview/getPreviewData",
            label="Toonflow points preview",
        ).get("data") or {}
        balance = safe_float(dashboard.get("totalPoints"))
        used = safe_float(dashboard.get("totalConsumption"))
    except Exception:
        pass
    try:
        pricing_metadata = _toonflow_pricing_metadata(session, origin)
    except Exception as exc:
        pricing_metadata = {
            "status": "unavailable",
            "error": clean_error(exc, (token,)),
            "models": [],
            "account_models": [],
            "fetched_at": int(time.time()),
        }
    prior = previous_balance(ledger, day, "toonflow")
    return complete_entry(
        "toonflow_web",
        rate,
        "",
        balance,
        used,
        raw_cost,
        len(evidence),
        {model: round(value, 8) for model, value in per_model.items()},
        model_real,
        prior,
        pricing_metadata=pricing_metadata,
        video_task_evidence=evidence,
    )


def parse_tiered_prices(sample):
    encoded = sample.get("expr_b64") or ""
    tier = sample.get("matched_tier") or ""
    if not encoded or not tier:
        return None
    try:
        expression = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return None
    match = re.search(
        rf'tier\("{re.escape(tier)}",\s*p\s*\*\s*([0-9.]+)\s*\+\s*c\s*\*\s*([0-9.]+)',
        expression,
    )
    return (float(match.group(1)), float(match.group(2))) if match else None


def classic_model_real_costs(details, rate):
    result = {}
    for model, detail in (details or {}).items():
        calls = int(detail.get("calls") or 0)
        quota = safe_float(detail.get("sum_quota"), 0.0) or 0.0
        prompt = int(detail.get("prompt_tokens") or 0)
        amount = quota / QUOTA_PER_USD * rate
        samples = detail.get("pricing_samples") or []
        per_second_samples = [
            sample
            for sample in samples
            if _positive_sample_price(sample.get("price_per_sec"))
            and sample.get("billing_type")
            in ("per_sec", "generation_failed_refund", "completed")
        ]
        if per_second_samples and len(per_second_samples) == len(samples):
            rates = {round(float(sample["price_per_sec"]) * rate, 12) for sample in samples}
            if len(rates) == 1:
                cost_per_second = rates.pop()
                net_seconds = 0.0
                successful_calls = 0
                successful_seconds = 0.0
                valid = True
                for sample in samples:
                    duration = safe_float(sample.get("duration"), 0.0) or 0.0
                    sample_quota = safe_float(sample.get("quota"), 0.0) or 0.0
                    billing_type = sample.get("billing_type")
                    if duration <= 0:
                        valid = False
                        break
                    if billing_type == "per_sec" and sample_quota > 0:
                        net_seconds += duration
                    elif billing_type == "generation_failed_refund" and sample_quota < 0:
                        net_seconds -= duration
                    elif billing_type == "completed" and sample_quota == 0:
                        successful_calls += 1
                        successful_seconds += duration
                    else:
                        valid = False
                        break
                expected = net_seconds * cost_per_second
                if valid and successful_calls and math.isclose(
                    amount, expected, rel_tol=0.0, abs_tol=0.000001
                ) and math.isclose(
                    net_seconds, successful_seconds, rel_tol=0.0, abs_tol=0.000001
                ):
                    result[model] = {
                        "kind": "video",
                        "billing_unit": "second",
                        "successful_calls": successful_calls,
                        "successful_output_seconds": round(successful_seconds, 6),
                        "net_cost_cny": round(amount, 6),
                        "cost_cny_per_second": round(cost_per_second, 6),
                        "pricing_source": "actual_deduction_log",
                    }
                    continue
        per_call_samples = [
            sample
            for sample in samples
            if _positive_sample_price(sample.get("price_per_call"))
            and sample.get("billing_type")
            in ("per_call", "generation_failed_refund", "completed")
        ]
        if per_call_samples and len(per_call_samples) == len(samples):
            rates = {round(float(sample["price_per_call"]) * rate, 12) for sample in samples}
            if len(rates) == 1:
                cost_per_call = rates.pop()
                net_calls = 0
                successful_calls = 0
                valid = True
                for sample in samples:
                    sample_quota = safe_float(sample.get("quota"), 0.0) or 0.0
                    billing_type = sample.get("billing_type")
                    if billing_type == "per_call" and sample_quota > 0:
                        net_calls += 1
                    elif billing_type == "generation_failed_refund" and sample_quota < 0:
                        net_calls -= 1
                    elif billing_type == "completed" and sample_quota == 0:
                        successful_calls += 1
                    else:
                        valid = False
                        break
                expected = net_calls * cost_per_call
                if valid and successful_calls and net_calls == successful_calls and math.isclose(
                    amount, expected, rel_tol=0.0, abs_tol=0.000001
                ):
                    result[model] = {
                        "kind": "video",
                        "billing_unit": "call",
                        "successful_calls": successful_calls,
                        "net_cost_cny": round(amount, 6),
                        "cost_cny_per_call": round(cost_per_call, 6),
                        "pricing_source": "actual_deduction_log",
                    }
                    continue
        lower_model = model.lower()
        is_video = any(
            word in lower_model
            for word in ("video", "seedance", "sd2-", "sd3-", "sd4-", "sora", "veo-")
        )
        if is_video and not prompt:
            result[model] = {
                "kind": "unclassified_video",
                "calls": calls,
                "net_cost_cny": round(amount, 6),
                "pricing_source": "unit_unverified",
            }
            continue
        is_fixed = any(word in lower_model for word in ("image", "seedream", "dall", "flux"))
        if is_fixed or not prompt:
            if calls:
                result[model] = {
                    "kind": "fixed",
                    "calls": calls,
                    "cost_cny_per_call": round(amount / calls, 6),
                }
            continue
        candidates = []
        for sample in detail.get("pricing_samples") or []:
            group_ratio = safe_float(sample.get("group_ratio"), 0.0) or 0.0
            if group_ratio <= 0:
                continue
            if sample.get("billing_mode") == "tiered_expr":
                tiered = parse_tiered_prices(sample)
                if tiered:
                    candidates.append((tiered[0] * group_ratio * rate, tiered[1] * group_ratio * rate, "tiered_expr"))
            else:
                model_ratio = safe_float(sample.get("model_ratio"), 0.0) or 0.0
                completion_ratio = safe_float(sample.get("completion_ratio"), 0.0) or 0.0
                if model_ratio > 0 and completion_ratio > 0:
                    input_price = model_ratio * 2.0 * group_ratio * rate
                    candidates.append((input_price, input_price * completion_ratio, "ratio"))
        info = {
            "kind": "text",
            "calls": calls,
            "effective_cost_cny_per_m_input": round(amount / prompt * 1_000_000, 6),
        }
        if candidates:
            input_price, output_price, source = max(candidates, key=lambda row: (row[0], row[1]))
            info.update(
                {
                    "input_cost_cny_per_m": round(input_price, 6),
                    "output_cost_cny_per_m": round(output_price, 6),
                    "pricing_source": source,
                }
            )
        else:
            info["input_cost_cny_per_m"] = info["effective_cost_cny_per_m_input"]
            info["pricing_source"] = "effective_fallback"
        result[model] = info
    return result


def v1_login(session, origin, username, password):
    response = session.post(
        origin + "/api/v1/auth/login",
        json={"email": username, "password": password},
        timeout=TIMEOUT,
    )
    body = json_response(response, "v1 login")
    data = body.get("data") or {}
    token = data.get("access_token")
    if response.status_code != 200 or not token:
        message = body.get("message") or body.get("reason")
        raise RuntimeError(f"v1 login failed (http {response.status_code}): {clean_error(message)}")
    session.headers.update({"Authorization": f"Bearer {token}"})


def v1_self(session, origin):
    response = session.get(origin + "/api/v1/auth/me", timeout=TIMEOUT)
    body = json_response(response, "v1 self")
    if response.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"v1 self failed: {clean_error(body.get('message'))}")
    return body.get("data") or {}


def v1_logs(session, origin, day):
    rows = []
    total_pages = None
    for page in range(1, MAX_PAGES + 1):
        response = session.get(
            origin + "/api/v1/usage",
            params={
                "start_date": day,
                "end_date": day,
                "page": page,
                "page_size": PAGE_SIZE,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            timeout=TIMEOUT,
        )
        body = json_response(response, f"v1 usage page {page}")
        if response.status_code != 200 or body.get("code") != 0:
            raise RuntimeError(f"v1 usage page {page} failed: {clean_error(body.get('message'))}")
        data = body.get("data") or {}
        items = data.get("items") or []
        rows.extend(items)
        total_pages = int(data.get("pages") or 0)
        if page >= total_pages or not items:
            break
    else:
        raise RuntimeError(f"v1 usage exceeds safety limit ({MAX_PAGES * PAGE_SIZE} rows)")
    expected = int((body.get("data") or {}).get("total") or len(rows))
    if len(rows) != expected:
        raise RuntimeError(f"v1 usage pagination incomplete: expected {expected}, got {len(rows)}")
    return rows


def aggregate_v1_rows(rows, rate):
    """Aggregate account deductions; total_cost is the amount charged to this account."""
    per_model = {}
    total = 0.0
    for item in rows:
        model = item.get("model") or "unknown"
        charged = safe_float(item.get("total_cost"), 0.0) or 0.0
        total += charged
        detail = per_model.setdefault(
            model,
            {
                "calls": 0,
                "total_cost": 0.0,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "image_count": 0,
            },
        )
        detail["calls"] += 1
        detail["total_cost"] += charged
        detail["input_cost"] += safe_float(item.get("input_cost"), 0.0) or 0.0
        detail["output_cost"] += safe_float(item.get("output_cost"), 0.0) or 0.0
        detail["input_tokens"] += int(item.get("input_tokens") or 0)
        detail["output_tokens"] += int(item.get("output_tokens") or 0)
        detail["image_count"] += int(item.get("image_count") or 0)

    model_totals = {}
    model_real = {}
    for model, detail in per_model.items():
        model_totals[model] = round(detail["total_cost"], 8)
        input_tokens = detail["input_tokens"]
        output_tokens = detail["output_tokens"]
        calls = detail["calls"]
        if input_tokens or output_tokens:
            info = {"kind": "text", "calls": calls, "pricing_source": "v1_usage_total_cost"}
            if input_tokens:
                info["input_cost_cny_per_m"] = round(
                    detail["input_cost"] / input_tokens * 1_000_000 * rate, 6
                )
            if output_tokens:
                info["output_cost_cny_per_m"] = round(
                    detail["output_cost"] / output_tokens * 1_000_000 * rate, 6
                )
            model_real[model] = info
        elif calls:
            divisor = detail["image_count"] or calls
            model_real[model] = {
                "kind": "fixed",
                "calls": calls,
                "cost_cny_per_call": round(detail["total_cost"] / divisor * rate, 6),
                "pricing_source": "v1_usage_total_cost",
            }
    return round(total, 8), model_totals, model_real


def previous_balance(ledger, day, slug):
    for prior_day in sorted((ledger.get("days") or {}).keys(), reverse=True):
        if prior_day == day:
            continue
        value = (((ledger.get("days") or {}).get(prior_day) or {}).get(slug) or {}).get("balance_usd")
        if value is not None:
            return value
    return None


def complete_entry(
    adapter,
    rate,
    group,
    balance,
    used,
    cost,
    rows,
    per_model,
    real_cost,
    prior,
    pricing_metadata=None,
    video_task_evidence=None,
):
    delta = None
    if prior is not None and balance is not None:
        change = round(prior - balance, 6)
        delta = change if change >= 0 else None
    return {
        "collection_status": "complete",
        "actual_log_complete": True,
        "collection_error": None,
        "billing_api": adapter,
        "balance_usd": balance,
        "used_quota_usd": used,
        "group": group,
        "rate": rate,
        "day_log_cost_usd": round(cost, 8),
        "day_log_cost_cny": round(cost * rate, 8),
        "day_log_rows": rows,
        "per_model_cost_usd": per_model,
        "per_model_real_cost": real_cost,
        "pricing_metadata": pricing_metadata,
        "video_task_evidence": video_task_evidence or [],
        "prev_balance_usd": prior,
        "day_balance_delta_usd": delta,
        "fetched_at": int(time.time()),
        "last_attempt_status": "complete",
        "last_attempt_at": int(time.time()),
    }


def collect_one(slug, credential, website, ledger, day):
    if str(slug or "").strip().lower() == "toonflow":
        return collect_toonflow(credential, website, ledger, day)
    username = credential.get("username")
    password = credential.get("password")
    origin = origin_of(credential.get("website_url") or website)
    rate = safe_float(credential.get("rate"), 1.0) or 1.0
    if not username or not password or not origin:
        raise RuntimeError("missing username/password/website_url")
    if urlsplit(origin).scheme != "https":
        raise RuntimeError("refusing to send account credentials over non-HTTPS transport")

    errors = []
    prior = previous_balance(ledger, day, slug)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    )
    try:
        group = standard_login(session, origin, username, password)
        self_data = standard_self(session, origin)
        rows, quota, per_model_quota, details = standard_logs(session, origin, day)
        real_cost = classic_model_real_costs(details, rate)
        try:
            pricing_metadata = standard_pricing_metadata(session, origin)
        except Exception as exc:
            pricing_metadata = {
                "status": "unavailable",
                "error": clean_error(exc, (username, password)),
                "models": [],
                "account_models": [],
                "fetched_at": int(time.time()),
            }
        try:
            video_task_evidence = standard_video_tasks(session, origin, day, slug, rate)
        except Exception as exc:
            video_task_evidence = []
            pricing_metadata["video_task_evidence_status"] = "unavailable"
            pricing_metadata["video_task_evidence_error"] = clean_error(
                exc, (username, password)
            )
        else:
            expected_video_cost = sum(
                float(detail.get("net_cost_cny") or 0)
                for detail in real_cost.values()
                if isinstance(detail, dict) and detail.get("kind") == "video"
            )
            video_task_evidence, evidence_status = validate_video_task_evidence(
                video_task_evidence, expected_video_cost
            )
            pricing_metadata["video_task_evidence_status"] = evidence_status
        return complete_entry(
            "newapi_classic",
            rate,
            group,
            q2usd(self_data.get("quota")),
            q2usd(self_data.get("used_quota")),
            quota / QUOTA_PER_USD,
            rows,
            {model: q2usd(value) for model, value in per_model_quota.items()},
            real_cost,
            prior,
            pricing_metadata=pricing_metadata,
            video_task_evidence=video_task_evidence,
        )
    except Exception as exc:
        errors.append(clean_error(exc, (username, password)))

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": origin,
            "Referer": origin + "/",
        }
    )
    try:
        v1_login(session, origin, username, password)
        self_data = v1_self(session, origin)
        usage_rows = v1_logs(session, origin, day)
        total, per_model, real_cost = aggregate_v1_rows(usage_rows, rate)
        return complete_entry(
            "usage_v1",
            rate,
            "",
            safe_float(self_data.get("balance")),
            None,
            total,
            len(usage_rows),
            per_model,
            real_cost,
            prior,
        )
    except Exception as exc:
        errors.append(clean_error(exc, (username, password)))
    raise RuntimeError("; ".join(errors))


def prior_is_complete(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("collection_status") == "complete" and entry.get("actual_log_complete") is True:
        return True
    return (
        entry.get("collection_status") is None
        and entry.get("day_log_rows") is not None
        and entry.get("day_log_cost_usd") is not None
    )


def failed_entry(prior_entry, error):
    now = int(time.time())
    if prior_is_complete(prior_entry):
        preserved = dict(prior_entry)
        preserved.update(
            {
                "collection_status": "complete",
                "actual_log_complete": True,
                "last_attempt_status": "incomplete",
                "last_attempt_error": clean_error(error),
                "last_attempt_at": now,
            }
        )
        return preserved
    return {
        "collection_status": "incomplete",
        "actual_log_complete": False,
        "collection_error": clean_error(error),
        "day_log_cost_usd": None,
        "day_log_cost_cny": None,
        "day_log_rows": None,
        "per_model_cost_usd": {},
        "per_model_real_cost": {},
        "fetched_at": now,
        "last_attempt_status": "incomplete",
        "last_attempt_error": clean_error(error),
        "last_attempt_at": now,
    }


def main():
    started = time.time()
    day = target_beijing_day()
    upstreams = read_json(UPSTREAMS_PATH, [])
    credentials = read_json(CRED_PATH, {})
    ledger = read_json(LEDGER_PATH, {"days": {}})
    website_by_slug = {item.get("slug"): item.get("website_url") for item in upstreams}
    day_entries = ledger.setdefault("days", {}).setdefault(day, {})
    results = []

    for slug in sorted(credentials):
        credential = credentials.get(slug)
        if not isinstance(credential, dict):
            continue
        try:
            entry = collect_one(slug, credential, website_by_slug.get(slug) or "", ledger, day)
            day_entries[slug] = entry
            results.append(
                {
                    "slug": slug,
                    "status": "complete",
                    "billing_api": entry.get("billing_api"),
                    "day_log_rows": entry.get("day_log_rows"),
                    "day_log_cost_cny": entry.get("day_log_cost_cny"),
                }
            )
        except Exception as exc:
            entry = failed_entry(day_entries.get(slug), exc)
            day_entries[slug] = entry
            results.append(
                {
                    "slug": slug,
                    "status": entry.get("last_attempt_status") or "incomplete",
                    "preserved_complete": entry.get("collection_status") == "complete",
                    "error": entry.get("last_attempt_error") or entry.get("collection_error"),
                }
            )

    while len(ledger.get("days") or {}) > 90:
        ledger["days"].pop(sorted(ledger["days"])[0], None)
    ledger["updated_at"] = int(time.time())
    ledger["updated_at_iso"] = beijing_iso_now()
    write_json(LEDGER_PATH, ledger)

    summary = {
        "date": day,
        "complete": sum(1 for row in results if row.get("status") == "complete"),
        "incomplete": sum(1 for row in results if row.get("status") != "complete"),
        "duration_seconds": round(time.time() - started, 2),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["incomplete"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
