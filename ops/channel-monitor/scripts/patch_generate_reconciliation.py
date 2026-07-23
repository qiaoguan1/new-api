#!/usr/bin/env python3
"""Patch the production monitor generator to emit dated reconciliation."""

import argparse
import ast
import os
from pathlib import Path


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def transform(source):
    source = replace_once(
        source,
        "from urllib.parse import urlparse\n",
        "from urllib.parse import urlparse\n\nfrom daily_reconciliation import build_reconciliation\n",
        "import",
    )
    source = replace_once(
        source,
        'UPSTREAM_LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"\n',
        'UPSTREAM_LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"\n'
        'UPSTREAM_CREDENTIALS_PATH = ROOT / "upstream-credentials.json"\n',
        "credential path",
    )
    source = replace_once(
        source,
        "    for slug, entry in ledger_day.items():\n        rate = float((entry or {}).get(\"rate\") or 1.0)\n",
        "    for slug, entry in ledger_day.items():\n"
        "        if (entry or {}).get(\"collection_status\") != \"complete\" or (entry or {}).get(\"actual_log_complete\") is not True:\n"
        "            continue\n"
        "        rate = float((entry or {}).get(\"rate\") or 1.0)\n",
        "complete ledger filter",
    )
    source = replace_once(
        source,
        '    risk_channels = sum(1 for r in business_channels if r["gross_margin"] is not None and r["gross_margin"] < 0.2)\n    return {\n',
        '    risk_channels = sum(1 for r in business_channels if r["gross_margin"] is not None and r["gross_margin"] < 0.2)\n'
        '    credentials = load_json(UPSTREAM_CREDENTIALS_PATH, {})\n'
        '    credential_slugs = credentials.keys() if isinstance(credentials, dict) else []\n'
        '    reconciliation = build_reconciliation(\n'
        '        upstreams, channels, audit, ledger, day, usage_rows, credential_slugs,\n'
        '        channel_matches_upstream, quota_to_usd,\n'
        '    )\n'
        '    business_complete = reconciliation.get("complete") is True\n'
        '    return {\n'
        '        "reconciliation": reconciliation,\n',
        "reconciliation payload",
    )
    source = replace_once(
        source,
        '            "gross_profit_cny": total_profit,\n            "gross_margin": safe_div(total_profit, total_revenue, 4),\n',
        '            "gross_profit_cny": total_profit if business_complete else None,\n'
        '            "gross_margin": safe_div(total_profit, total_revenue, 4) if business_complete else None,\n'
        '            "reconciliation_complete": business_complete,\n',
        "fail closed totals",
    )
    ast.parse(source)
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    updated = transform(args.target.read_text(encoding="utf-8"))
    if not args.check:
        temporary = args.target.with_name(args.target.name + ".reconciliation.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(f"reconciliation generator patch verified: write={not args.check}")


if __name__ == "__main__":
    main()
