#!/usr/bin/env python3
"""Aggregate authenticated upstream recharge records without retaining orders."""

import importlib.util
import json
import math
import os
import pathlib
import sys
import time
from urllib.parse import urlsplit

import requests


ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAMS_PATH = ROOT / "upstreams.json"
CREDENTIALS_PATH = ROOT / "upstream-credentials.json"
OUTPUT_PATH = ROOT / "data" / "upstream-recharge-summary.json"
BALANCE_SCRIPT_PATH = pathlib.Path(__file__).with_name("fetch-upstream-balance.py")
PAGE_SIZE = 100
MAX_PAGES = 100


def _load_balance_collector():
    spec = importlib.util.spec_from_file_location(
        "channel_monitor_fetch_upstream_balance", BALANCE_SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("cannot load upstream balance collector")
    spec.loader.exec_module(module)
    return module


def read_json(path, *, required=False):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        if required:
            raise
        return {}


def write_private_json(path, value):
    path = pathlib.Path(path)
    temporary = pathlib.Path(str(path) + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _epoch(value):
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number > 1_000_000_000_000:
        number /= 1000
    return int(number)


def validate_toonflow_origin(origin):
    parsed = urlsplit(str(origin or ""))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "api.toonflow.net"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError("unapproved Toonflow host")
    return "https://api.toonflow.net"


def _paid_value(record):
    currency = str(
        record.get("provider_currency") or record.get("currency") or "CNY"
    ).strip().upper() or "CNY"
    fields = (
        "paid_amount_cny",
        "actual_money",
        "paid_amount",
        "provider_amount",
        "amount",
    )
    zero_candidate = None
    for field in fields:
        value = _number(record.get(field))
        if value is not None and value > 0:
            if field.endswith("_cny") or field == "actual_money":
                currency = "CNY"
            return currency, value
        if value == 0 and zero_candidate is None:
            zero_candidate = (currency, value)
    value = _number(record.get("money"))
    if value is not None and value > 0:
        return currency, value
    return zero_candidate or (None, None)


def summarize_recharges(records):
    successful = [
        row
        for row in records
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower() == "success"
    ]
    credited = 0.0
    credited_known = True
    paid_amounts = {}
    created = []
    completed = []
    for row in successful:
        money = _number(row.get("money"))
        if money is None:
            credited_known = False
        else:
            credited += money
        currency, paid = _paid_value(row)
        if currency is not None and paid is not None:
            paid_amounts[currency] = paid_amounts.get(currency, 0.0) + paid
        created_at = _epoch(row.get("create_time"))
        completed_at = _epoch(row.get("complete_time"))
        if created_at is not None:
            created.append(created_at)
        if completed_at is not None:
            completed.append(completed_at)
    return {
        "successful_records": len(successful),
        "credited_amount": round(credited, 6) if credited_known else None,
        "credited_unit": "upstream_account_money",
        "paid_amounts": {
            key: round(value, 6) for key, value in sorted(paid_amounts.items())
        },
        "first_record_at": min(created) if created else None,
        "last_completed_at": max(completed) if completed else None,
    }


def fetch_classic_recharges(
    session, origin, *, page_size=PAGE_SIZE, max_pages=MAX_PAGES
):
    rows = []
    expected_total = None
    for page in range(1, max_pages + 1):
        response = session.get(
            origin + "/api/user/topup/self",
            params={"p": page, "page_size": page_size},
            timeout=25,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"recharge history returned non-json (http {response.status_code})"
            ) from exc
        data = body.get("data") if isinstance(body, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if (
            response.status_code != 200
            or not body.get("success")
            or not isinstance(items, list)
        ):
            raise RuntimeError(
                f"recharge history unavailable (http {response.status_code})"
            )
        total = data.get("total")
        if total is not None:
            expected_total = int(total)
        rows.extend(item for item in items if isinstance(item, dict))
        if not items or (expected_total is not None and len(rows) >= expected_total):
            break
    if expected_total is not None and len(rows) < expected_total:
        raise RuntimeError(
            f"recharge pagination incomplete: expected {expected_total}, got {len(rows)}"
        )
    if len(rows) >= page_size * max_pages and expected_total is None:
        raise RuntimeError("recharge pagination reached safety limit")
    return rows


def fetch_toonflow_recharges(
    session, origin, *, page_size=PAGE_SIZE, max_pages=MAX_PAGES
):
    rows = []
    expected_total = None
    for page in range(1, max_pages + 1):
        response = session.get(
            origin + "/web/web/order/getOrder",
            params={"page": page, "limit": page_size},
            timeout=25,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Toonflow order history returned non-json (http {response.status_code})"
            ) from exc
        data = body.get("data") if isinstance(body, dict) else None
        items = data.get("data") if isinstance(data, dict) else None
        if (
            response.status_code != 200
            or body.get("code") != 200
            or not isinstance(items, list)
        ):
            raise RuntimeError(
                f"Toonflow order history unavailable (http {response.status_code})"
            )
        total = data.get("total")
        if total is not None:
            expected_total = int(total)
        rows.extend(item for item in items if isinstance(item, dict))
        if not items or (expected_total is not None and len(rows) >= expected_total):
            break
    if expected_total is not None and len(rows) < expected_total:
        raise RuntimeError(
            f"Toonflow order pagination incomplete: expected {expected_total}, got {len(rows)}"
        )
    successful = [row for row in rows if _number(row.get("status")) == 2]
    points = [_number(row.get("points")) for row in successful]
    amounts = [_number(row.get("amount")) for row in successful]
    type_names = {1: "admin_recharge", 2: "consumption_recharge", 3: "code_exchange"}
    type_counts = {}
    for row in successful:
        numeric_type = _number(row.get("type"))
        type_id = int(numeric_type) if numeric_type is not None else 0
        label = type_names.get(type_id, f"type_{type_id}")
        type_counts[label] = type_counts.get(label, 0) + 1
    created = [_epoch(row.get("creationTime")) for row in successful]
    completed = [_epoch(row.get("paymentTime")) for row in successful]
    return {
        "successful_records": len(successful),
        "credited_amount": (
            round(sum(value for value in points if value is not None), 6)
            if all(value is not None for value in points)
            else None
        ),
        "credited_unit": "toonflow_points",
        "paid_amounts": {
            "CNY": round(sum(value for value in amounts if value is not None), 6)
        }
        if amounts and all(value is not None for value in amounts)
        else {},
        "type_counts": dict(sorted(type_counts.items())),
        "first_record_at": min((value for value in created if value), default=None),
        "last_completed_at": max(
            (value for value in completed if value), default=None
        ),
    }


def select_targets(upstreams, credentials):
    targets = []
    for item in upstreams if isinstance(upstreams, list) else []:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        slug = str(item.get("slug") or "").strip()
        credential = credentials.get(slug) if isinstance(credentials, dict) else None
        if not slug or not isinstance(credential, dict):
            continue
        targets.append(
            (
                slug,
                str(item.get("name") or slug).strip() or slug,
                str(item.get("website_url") or "").strip(),
                credential,
            )
        )
    return sorted(targets, key=lambda row: row[0])


def collect_classic(balance_collector, credential, website):
    username = credential.get("username")
    password = credential.get("password")
    origin = balance_collector.origin_of(credential.get("website_url") or website)
    if not username or not password or not origin:
        raise RuntimeError("missing account configuration")
    if not origin.startswith("https://"):
        raise RuntimeError("refusing non-HTTPS authentication")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": balance_collector.UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    balance_collector.standard_login(session, origin, username, password)
    self_data = balance_collector.standard_self(session, origin)
    records = fetch_classic_recharges(session, origin)
    result = summarize_recharges(records)
    result.update(
        {
            "status": "complete",
            "adapter": "newapi_classic_topup_self",
            "current_balance_usd": balance_collector.q2usd(self_data.get("quota")),
        }
    )
    return result


def collect_provider(balance_collector, slug, credential, website):
    if slug == "toonflow":
        origin = validate_toonflow_origin(
            balance_collector.origin_of(credential.get("website_url") or website)
        )
        token = balance_collector._toonflow_token(credential)
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": balance_collector.UA,
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Origin": origin,
                "Referer": origin + "/",
            }
        )
        result = fetch_toonflow_recharges(session, origin)
        probe = balance_collector.probe_balance(slug, credential, website)
        result.update(
            {
                "status": "complete",
                "adapter": "toonflow_web_order_history",
                "current_balance_usd": probe.get("balance_usd"),
            }
        )
        return result
    try:
        return collect_classic(balance_collector, credential, website)
    except Exception as exc:
        # A usage-v1 account may still expose a balance, but no recharge-history
        # contract is known. Never infer recharge from that balance.
        balance = None
        try:
            balance = balance_collector.probe_balance(slug, credential, website).get(
                "balance_usd"
            )
        except Exception:
            pass
        return {
            "status": "unavailable",
            "adapter": "unknown",
            "current_balance_usd": balance,
            "unavailable_reason": balance_collector.clean_error(
                exc, (credential.get("username"), credential.get("password"))
            ),
        }


def main():
    balance_collector = _load_balance_collector()
    upstreams = read_json(UPSTREAMS_PATH, required=True)
    credentials = read_json(CREDENTIALS_PATH, required=True)
    providers = {}
    for slug, name, website, credential in select_targets(upstreams, credentials):
        result = collect_provider(balance_collector, slug, credential, website)
        providers[slug] = {"name": name, **result}
    payload = {
        "generated_at": int(time.time()),
        "source": "authenticated_upstream_recharge_records",
        "providers": providers,
        "complete": sum(row.get("status") == "complete" for row in providers.values()),
        "unavailable": sum(
            row.get("status") != "complete" for row in providers.values()
        ),
    }
    write_private_json(OUTPUT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
