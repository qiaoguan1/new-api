#!/usr/bin/env python3
"""Create price-safety alerts from complete model-level actual billing costs."""

import math

from channel_audit_policy import (
    STABLE_MAPPED_MODELS,
    parse_model_mapping,
    resolve_model_mapping,
)


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

    try:
        mapping = parse_model_mapping(row.get("model_mapping"))
    except ValueError as exc:
        return [
            {
                "type": "model_mapping_invalid",
                "channel_id": row.get("channel_id"),
                "channel_name": row.get("name"),
                "severity": "critical",
                "error": str(exc),
            }
        ]

    alerts = []
    for model, model_detail in channel_models.items():
        if not isinstance(model, str) or not model or not isinstance(model_detail, dict):
            continue
        if model_detail.get("available") is False:
            continue
        price_model = model
        if model in STABLE_MAPPED_MODELS:
            try:
                price_model = resolve_model_mapping(model, mapping)
            except ValueError as exc:
                alerts.append(
                    {
                        "type": "model_mapping_invalid",
                        "channel_id": row.get("channel_id"),
                        "channel_name": row.get("name"),
                        "model": model,
                        "severity": "critical",
                        "error": str(exc),
                    }
                )
                continue
            audited_upstream = str(model_detail.get("upstream_model") or model)
            if audited_upstream != price_model:
                alerts.append(
                    {
                        "type": "model_mapping_mismatch",
                        "channel_id": row.get("channel_id"),
                        "channel_name": row.get("name"),
                        "model": model,
                        "severity": "critical",
                        "request_upstream_model": price_model,
                        "audit_upstream_model": audited_upstream,
                    }
                )
                continue
        cost = model_costs.get(price_model)
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
