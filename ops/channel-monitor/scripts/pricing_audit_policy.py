#!/usr/bin/env python3
"""Create price-safety alerts from complete model-level actual billing costs."""

import math


def _positive_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def actual_cost_alerts(row, ledger_entry, sell_price_getter, settings):
    """Compare local sell prices only with complete, dated actual-cost samples."""
    if (
        not isinstance(row, dict)
        or not isinstance(ledger_entry, dict)
        or ledger_entry.get("collection_status") != "complete"
        or ledger_entry.get("actual_log_complete") is not True
    ):
        return []
    model_costs = ledger_entry.get("per_model_real_cost") or {}
    if not isinstance(model_costs, dict):
        return []
    channel_models = row.get("models") or {}
    if not isinstance(channel_models, dict):
        return []

    alerts = []
    for model, upstream_model in channel_models.items():
        if not isinstance(model, str) or not model or not isinstance(upstream_model, dict):
            continue
        if upstream_model.get("available") is False:
            continue
        cost = model_costs.get(model)
        if not isinstance(cost, dict) or cost.get("kind") != "text":
            continue
        input_cost = cost.get("input_cost_cny_per_m")
        output_cost = cost.get("output_cost_cny_per_m")
        if not _positive_number(input_cost) or not _positive_number(output_cost):
            continue
        sell_input, sell_output = sell_price_getter(
            model, row.get("group") or "", settings
        )
        common = {
            "channel_id": row.get("channel_id"),
            "channel_name": row.get("name"),
            "model": model,
            "severity": "critical",
            "cost_evidence": "complete_actual_billing_log",
        }
        if _positive_number(sell_input) and float(sell_input) < float(input_cost):
            alerts.append(
                {
                    **common,
                    "type": "price_below_actual_input",
                    "sell_input_cny_per_m": float(sell_input),
                    "actual_input_cost_cny_per_m": float(input_cost),
                }
            )
        if _positive_number(sell_output) and float(sell_output) < float(output_cost):
            alerts.append(
                {
                    **common,
                    "type": "price_below_actual_output",
                    "sell_output_cny_per_m": float(sell_output),
                    "actual_output_cost_cny_per_m": float(output_cost),
                }
            )
    return alerts
