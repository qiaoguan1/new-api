#!/usr/bin/env python3
"""Patch the deployed pricing worker to use recent trusted actual samples."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


IMPORT_OLD = "from monitor_time import beijing_now, resolve_beijing_business_day\n"
IMPORT_NEW = '''from monitor_time import beijing_now, resolve_beijing_business_day
from recent_actual_cost import collect_recent_model_costs
'''

CONSTANT_OLD = "DEFAULT_MAX_CHANGE_RATIO = 5.0\n"
CONSTANT_NEW = '''DEFAULT_MAX_CHANGE_RATIO = 5.0
ACTUAL_COST_LOOKBACK_DAYS = 7
'''

COLLECT_OLD = '''        costs = _collect_model_costs(day_rows, model, eligible_sources)
'''
COLLECT_NEW = '''        costs = collect_recent_model_costs(
            ledger,
            day,
            model,
            eligible_sources,
            lookback_days=ACTUAL_COST_LOOKBACK_DAYS,
        )
'''

TEXT_UNPACK_OLD = '''            worst_input, input_source = costs["text_input"][0]
            worst_output, output_source = costs["text_output"][0]
            new_ratio = worst_input * BASE_MULTIPLIER / 2.0
'''
TEXT_UNPACK_NEW = '''            worst_input, input_source, input_sample_date = costs["text_input"][0]
            worst_output, output_source, output_sample_date = costs["text_output"][0]
            cost_basis = (
                "current_day_actual"
                if input_sample_date == day and output_sample_date == day
                else "recent_actual"
            )
            new_ratio = worst_input * BASE_MULTIPLIER / 2.0
'''

TEXT_FIELDS_OLD = '''                        "worst_input_source": input_source,
                        "worst_output_cost_cny_per_m": worst_output,
                        "worst_output_source": output_source,
                        "new_model_ratio": round(new_ratio, 12),
'''
TEXT_FIELDS_NEW = '''                        "worst_input_source": input_source,
                        "worst_input_sample_date": input_sample_date,
                        "worst_output_cost_cny_per_m": worst_output,
                        "worst_output_source": output_source,
                        "worst_output_sample_date": output_sample_date,
                        "cost_basis": cost_basis,
                        "actual_cost_lookback_days": ACTUAL_COST_LOOKBACK_DAYS,
                        "new_model_ratio": round(new_ratio, 12),
'''

FIXED_UNPACK_OLD = '''            worst_cost, source = costs["fixed"][0]
            new_price = worst_cost * BASE_MULTIPLIER
'''
FIXED_UNPACK_NEW = '''            worst_cost, source, sample_date = costs["fixed"][0]
            cost_basis = "current_day_actual" if sample_date == day else "recent_actual"
            new_price = worst_cost * BASE_MULTIPLIER
'''

FIXED_FIELDS_OLD = '''                        "worst_cost_cny_per_call": worst_cost,
                        "worst_source": source,
                        "new_model_price": round(new_price, 12),
'''
FIXED_FIELDS_NEW = '''                        "worst_cost_cny_per_call": worst_cost,
                        "worst_source": source,
                        "worst_cost_sample_date": sample_date,
                        "cost_basis": cost_basis,
                        "actual_cost_lookback_days": ACTUAL_COST_LOOKBACK_DAYS,
                        "new_model_price": round(new_price, 12),
'''

SUMMARY_OLD = '''                    "worst_input_source",
                    "worst_output_cost_cny_per_m",
                    "worst_output_source",
                    "worst_cost_cny_per_call",
                    "worst_source",
'''
SUMMARY_NEW = '''                    "worst_input_source",
                    "worst_input_sample_date",
                    "worst_output_cost_cny_per_m",
                    "worst_output_source",
                    "worst_output_sample_date",
                    "worst_cost_cny_per_call",
                    "worst_source",
                    "worst_cost_sample_date",
                    "cost_basis",
                    "actual_cost_lookback_days",
'''

DISCOVERY_OLD = '''    discovered = set(policy["discovered_models"])
    for slug in policy["healthy_sources"]:
        entry = day_rows.get(slug) or {}
        costs = entry.get("per_model_real_cost") or {}
        if isinstance(costs, dict):
            discovered.update(name for name in costs if isinstance(name, str) and name)
'''
DISCOVERY_NEW = '''    # Current healthy enabled channel configuration defines inventory. Billing
    # history can price configured models but must not resurrect retired ones.
    discovered = set(policy["discovered_models"])
'''


REPLACEMENTS = (
    (IMPORT_OLD, IMPORT_NEW, "recent-cost import"),
    (CONSTANT_OLD, CONSTANT_NEW, "lookback constant"),
    (COLLECT_OLD, COLLECT_NEW, "cost collection"),
    (TEXT_UNPACK_OLD, TEXT_UNPACK_NEW, "text sample unpack"),
    (TEXT_FIELDS_OLD, TEXT_FIELDS_NEW, "text evidence fields"),
    (FIXED_UNPACK_OLD, FIXED_UNPACK_NEW, "fixed sample unpack"),
    (FIXED_FIELDS_OLD, FIXED_FIELDS_NEW, "fixed evidence fields"),
    (SUMMARY_OLD, SUMMARY_NEW, "summary evidence fields"),
    (DISCOVERY_OLD, DISCOVERY_NEW, "configured inventory"),
)


def patch_text(source: str) -> tuple[str, bool]:
    if all(new in source for _, new, _ in REPLACEMENTS):
        return source, False
    updated = source
    changed = False
    for old, new, label in REPLACEMENTS:
        if new in updated:
            continue
        count = updated.count(old)
        if count != 1:
            raise ValueError(f"expected exactly one {label} block, found {count}")
        updated = updated.replace(old, new, 1)
        changed = True
    return updated, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    updated, changed = patch_text(source)
    if changed:
        temporary = args.target.with_name(f".{args.target.name}.issue30.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode)
        os.replace(temporary, args.target)
        print(f"patched {args.target}")
    else:
        print(f"already patched {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
