#!/usr/bin/env python3
"""Patch the production monitor generator to use active-only health policy."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


IMPORT_OLD = "from daily_reconciliation import build_reconciliation\n"
IMPORT_NEW = """from daily_reconciliation import build_reconciliation
from monitor_health_policy import (
    classify_health,
    is_alert,
    is_warning,
    summarize_enabled_channels,
)
"""

HEALTH_OLD = '''def health_for(row):
    enabled = row["enabled_channels"]
    errors = row["calls_24h"] - row["success_24h"]
    response = row["avg_response_ms"]
    db_balance = row["db_balance"]

    if enabled == 0:
        return "down"
    if errors >= 5 or row["error_rate_24h"] >= 0.2:
        return "error"
    if response >= 5000:
        return "slow"
    if db_balance is not None and db_balance < 1:
        return "low_balance"
    return "ok"
'''

HEALTH_NEW = '''def health_for(row):
    return classify_health(row)
'''

DB_OLD = '''    db = run_psql_json(sql)
    channels = db.get("channels", [])
'''

DB_NEW = '''    db = run_psql_json(sql)
    channels = db.get("channels", [])
    generated_at = int(db.get("generated_at") or time.time())
'''

AGGREGATION_OLD = '''        enabled = [c for c in related if c.get("status") == 1]
        db_balances = [float(c["balance"]) for c in related if c.get("balance") is not None]
        calls_24h = sum(int(c.get("calls_24h") or 0) for c in related)
        success_24h = sum(int(c.get("success_24h") or 0) for c in related)
        errors_24h = sum(int(c.get("errors_24h") or 0) for c in related)
        prompt_tokens_24h = sum(int(c.get("prompt_tokens_24h") or 0) for c in related)
        completion_tokens_24h = sum(int(c.get("completion_tokens_24h") or 0) for c in related)
        cost_24h_usd = quota_to_usd(sum(int(c.get("quota_24h") or 0) for c in related))
        response_values = [int(c.get("response_time") or 0) for c in related if int(c.get("response_time") or 0) > 0]
        row = {
            **upstream,
            "channels": related,
            "channel_count": len(related),
            "enabled_channels": len(enabled),
            "calls_24h": calls_24h,
            "success_24h": success_24h,
            "errors_24h": errors_24h,
            "error_rate_24h": round(errors_24h / calls_24h, 4) if calls_24h else 0,
            "cost_24h_usd": cost_24h_usd,
            "dynamic_pricing_24h": build_dynamic_pricing(cost_24h_usd, calls_24h, success_24h, prompt_tokens_24h, completion_tokens_24h),
            "cost_7d_usd": quota_to_usd(sum(int(c.get("quota_7d") or 0) for c in related)),
            "used_usd": quota_to_usd(sum(int(c.get("used_quota") or 0) for c in related)),
            "db_balance": round(sum(db_balances), 6) if db_balances else None,
            "avg_response_ms": round(sum(response_values) / len(response_values)) if response_values else 0,
            "last_test_at": max((int(c.get("test_time") or 0) for c in related), default=0),
            "last_call_at": max((int(c.get("last_call_at") or 0) for c in related), default=0),
            "last_error": next((c.get("last_error") for c in related if c.get("last_error")), ""),
        }
        row["health"] = health_for(row)
'''

AGGREGATION_NEW = '''        active = summarize_enabled_channels(related, generated_at)
        calls_24h = active["calls_24h"]
        success_24h = active["success_24h"]
        prompt_tokens_24h = active["prompt_tokens_24h"]
        completion_tokens_24h = active["completion_tokens_24h"]
        cost_24h_usd = quota_to_usd(active["quota_24h"])
        row = {
            **upstream,
            "channels": related,
            "channel_count": len(related),
            **active,
            "cost_24h_usd": cost_24h_usd,
            "dynamic_pricing_24h": build_dynamic_pricing(cost_24h_usd, calls_24h, success_24h, prompt_tokens_24h, completion_tokens_24h),
            "cost_7d_usd": quota_to_usd(active["quota_7d"]),
            "used_usd": quota_to_usd(active["used_quota"]),
        }
        row["health"] = health_for(row)
'''

TOTALS_OLD = '''        "alerts": sum(1 for row in rows if row["health"] != "ok"),
'''
TOTALS_STAGE1 = '''        "alerts": sum(1 for row in rows if is_alert(row["health"])),
        "warnings": sum(1 for row in rows if is_warning(row["health"])),
'''

PAYLOAD_OLD = '''        "generated_at": db.get("generated_at") or int(time.time()),
'''
PAYLOAD_NEW = '''        "generated_at": generated_at,
'''

UNMATCHED_OLD = '''    unmatched = [c for c in channels if c["id"] not in matched_ids and c.get("priority", 0) >= 1]
'''
UNMATCHED_NEW = '''    unmatched = [
        c
        for c in channels
        if c["id"] not in matched_ids
        and (c.get("status") == 1 or c.get("priority", 0) >= 1)
    ]
    unmatched_enabled = [c for c in unmatched if c.get("status") == 1]
    unmatched_enabled_health = [
        health_for(summarize_enabled_channels([channel], generated_at))
        for channel in unmatched_enabled
    ]
    all_active = summarize_enabled_channels(channels, generated_at)
'''

TOTALS_BLOCK_OLD = '''    totals = {
        "upstreams": len(rows),
        "channels": len(channels),
        "enabled_channels": sum(1 for c in channels if c.get("status") == 1),
        "calls_24h": sum(row["calls_24h"] for row in rows),
        "errors_24h": sum(row["errors_24h"] for row in rows),
        "cost_24h_usd": round(sum(row["cost_24h_usd"] for row in rows), 6),
        "cost_7d_usd": round(sum(row["cost_7d_usd"] for row in rows), 6),
        "used_usd": round(sum(row["used_usd"] for row in rows), 6),
        "alerts": sum(1 for row in rows if is_alert(row["health"])),
        "warnings": sum(1 for row in rows if is_warning(row["health"])),
    }
'''

TOTALS_BLOCK_NEW = '''    totals = {
        "upstreams": len(rows),
        "channels": len(channels),
        "enabled_channels": all_active["enabled_channels"],
        "monitored_enabled_channels": all_active["enabled_channels"] - len(unmatched_enabled),
        "unmatched_enabled_channels": len(unmatched_enabled),
        "calls_24h": all_active["calls_24h"],
        "errors_24h": all_active["errors_24h"],
        "cost_24h_usd": quota_to_usd(all_active["quota_24h"]),
        "cost_7d_usd": quota_to_usd(all_active["quota_7d"]),
        "used_usd": quota_to_usd(all_active["used_quota"]),
        "alerts": (
            sum(1 for row in rows if is_alert(row["health"]))
            + sum(1 for health in unmatched_enabled_health if is_alert(health))
        ),
        "warnings": (
            sum(1 for row in rows if is_warning(row["health"]))
            + len(unmatched_enabled)
        ),
    }
'''


REPLACEMENTS = (
    (IMPORT_OLD, IMPORT_NEW, "policy import"),
    (HEALTH_OLD, HEALTH_NEW, "health classifier"),
    (DB_OLD, DB_NEW, "generated timestamp"),
    (AGGREGATION_OLD, AGGREGATION_NEW, "active-only aggregation"),
    (TOTALS_OLD, TOTALS_STAGE1, "alert totals"),
    (PAYLOAD_OLD, PAYLOAD_NEW, "payload timestamp"),
)

FOLLOWUP_REPLACEMENTS = (
    (UNMATCHED_OLD, UNMATCHED_NEW, "unmatched enabled channels"),
    (TOTALS_BLOCK_OLD, TOTALS_BLOCK_NEW, "all-enabled totals"),
)


def patch_text(source: str) -> tuple[str, bool]:
    if (
        IMPORT_NEW in source
        and AGGREGATION_NEW in source
        and TOTALS_BLOCK_NEW in source
        and UNMATCHED_NEW in source
    ):
        return source, False
    updated = source
    if IMPORT_NEW not in updated:
        for old, new, label in REPLACEMENTS:
            count = updated.count(old)
            if count != 1:
                raise ValueError(f"expected exactly one {label} block, found {count}")
            updated = updated.replace(old, new, 1)
    for old, new, label in FOLLOWUP_REPLACEMENTS:
        count = updated.count(old)
        if count != 1:
            raise ValueError(f"expected exactly one {label} block, found {count}")
        updated = updated.replace(old, new, 1)
    return updated, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    updated, changed = patch_text(source)
    if changed:
        temporary = args.target.with_name(f".{args.target.name}.issue28.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode)
        os.replace(temporary, args.target)
        print(f"patched {args.target}")
    else:
        print(f"already patched {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
