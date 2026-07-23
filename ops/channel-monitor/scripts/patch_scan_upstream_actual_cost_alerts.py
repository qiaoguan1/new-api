#!/usr/bin/env python3
"""Patch the production daily scan to alert from actual model billing costs."""

import argparse
import ast
import os
from pathlib import Path


IMPORT = "from pricing_audit_policy import actual_cost_alerts\n"
IMPORT_ANCHOR = "from channel_audit_policy import (\n"

CATALOG_ALERT_BLOCK = '''        for model, upstream_price in (row.get("models") or {}).items():
            if not upstream_price.get("base_input_usd_per_m"):
                continue
            sell_input, sell_output = local_sell_price(model, row.get("group") or "", settings)
            if sell_input and sell_input < upstream_price["base_input_usd_per_m"]:
                alerts.append({
                    "type": "price_below_upstream_input",
                    "channel_id": row.get("channel_id"),
                    "channel_name": row.get("name"),
                    "model": model,
                    "sell_input_usd_per_m": sell_input,
                    "upstream_input_usd_per_m": upstream_price["base_input_usd_per_m"],
                    "severity": "critical",
                })
'''

ACTUAL_ALERT_BLOCK = '''        alerts.extend(
            actual_cost_alerts(
                row,
                balance_ledger.get(row.get("upstream_slug") or "") or {},
                local_sell_price,
                settings,
            )
        )
'''


def transform(source):
    """Return an idempotently patched scanner or reject an unknown source."""
    if IMPORT in source and ACTUAL_ALERT_BLOCK in source:
        ast.parse(source)
        return source
    if source.count(IMPORT_ANCHOR) != 1 or source.count(CATALOG_ALERT_BLOCK) != 1:
        raise RuntimeError("scanner does not contain the expected catalog-price alert block")
    source = source.replace(IMPORT_ANCHOR, IMPORT + "\n" + IMPORT_ANCHOR, 1)
    source = source.replace(CATALOG_ALERT_BLOCK, ACTUAL_ALERT_BLOCK, 1)
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
        raise RuntimeError("target still uses catalog-price underpricing alerts")
    if not args.check and updated != source:
        temporary = args.target.with_name(args.target.name + ".actual-cost-alerts.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(
        "actual-cost scan patch verified: "
        f"mode={'check' if args.check else 'apply'}, changed={updated != source}"
    )


if __name__ == "__main__":
    main()

