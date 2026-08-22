#!/usr/bin/env python3
"""Select recent model-level actual deduction samples without guessing costs."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable


DEFAULT_LOOKBACK_DAYS = 7
APPROVED_TEXT_SOURCES = {
    "v1_usage_actual_cost",
    "classic_usage_actual_pricing",
}
APPROVED_FIXED_SOURCES = {
    "v1_usage_actual_cost",
    "classic_usage_actual_quota",
}
MIN_EVIDENCE_RATIO = 0.01
MAX_CLASSIC_EVIDENCE_RATIO = 2.0
MAX_V1_EVIDENCE_RATIO = 1.000001


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _collection_complete(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("collection_status") == "complete"
        and entry.get("actual_log_complete") is True
    )


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _close(first: float, second: float) -> bool:
    return math.isclose(first, second, rel_tol=0.000001, abs_tol=0.000001)


def _valid_cost(info: Any) -> tuple[str, float, float | None] | None:
    if not isinstance(info, dict):
        return None
    source = info.get("pricing_source")
    calls = info.get("calls")
    billed_total = info.get("billed_cost_total_cny")
    reference_total = info.get("reference_cost_total_cny")
    if (
        info.get("evidence_closed") is not True
        or not _positive_integer(calls)
        or not _positive_number(billed_total)
    ):
        return None
    kind = info.get("kind")
    if kind == "text":
        if source not in APPROVED_TEXT_SOURCES or not _positive_number(reference_total):
            return None
        input_cost = info.get("input_cost_cny_per_m")
        output_cost = info.get("output_cost_cny_per_m")
        input_tokens = info.get("input_tokens")
        output_tokens = info.get("output_tokens")
        if not (
            _positive_number(input_cost)
            and _positive_number(output_cost)
            and isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            and output_tokens >= 0
            and input_tokens + output_tokens > 0
        ):
            return None
        if source == "v1_usage_actual_cost":
            billed_ratio = float(billed_total) / float(reference_total)
            if not MIN_EVIDENCE_RATIO <= billed_ratio <= MAX_V1_EVIDENCE_RATIO:
                return None
            component_keys = (
                "billed_input_cost_total_cny",
                "billed_output_cost_total_cny",
                "billed_cache_cost_total_cny",
                "billed_image_component_cost_total_cny",
            )
            components = [info.get(key) for key in component_keys]
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
                for value in components
            ):
                return None
            if not _close(sum(float(value) for value in components), float(billed_total)):
                return None
            if input_tokens and not _close(
                float(input_cost) * input_tokens / 1_000_000,
                float(info["billed_input_cost_total_cny"]),
            ):
                return None
            if output_tokens and not _close(
                float(output_cost) * output_tokens / 1_000_000,
                float(info["billed_output_cost_total_cny"]),
            ):
                return None
        else:
            reconstructed = (
                float(input_cost) * input_tokens
                + float(output_cost) * output_tokens
            ) / 1_000_000
            if not _close(reconstructed, float(billed_total)):
                return None
            ratio = float(billed_total) / float(reference_total)
            if not MIN_EVIDENCE_RATIO <= ratio <= MAX_CLASSIC_EVIDENCE_RATIO:
                return None
        return "text", float(input_cost), float(output_cost)
    if kind == "fixed" and source in APPROVED_FIXED_SOURCES:
        units = info.get("billed_units")
        billing_unit = info.get("billing_unit")
        price_key = "cost_cny_per_image" if billing_unit == "image" else "cost_cny_per_call"
        value = info.get(price_key)
        if (
            not _positive_number(value)
            or not _positive_integer(units)
            or billing_unit not in {"request", "image"}
            or not _close(float(value) * units, float(billed_total))
        ):
            return None
        if source == "v1_usage_actual_cost":
            if not _positive_number(reference_total):
                return None
            billed_ratio = float(billed_total) / float(reference_total)
            if not MIN_EVIDENCE_RATIO <= billed_ratio <= MAX_V1_EVIDENCE_RATIO:
                return None
        return "fixed", float(value), None
    return None


def _window_dates(target_day: str, lookback_days: int) -> list[str]:
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool):
        raise ValueError("lookback_days must be an integer")
    if lookback_days < 1 or lookback_days > 31:
        raise ValueError("lookback_days must be between 1 and 31")
    target = dt.date.fromisoformat(target_day)
    return [
        (target - dt.timedelta(days=offset)).isoformat()
        for offset in range(lookback_days)
    ]


def collect_recent_model_costs(
    ledger: dict[str, Any],
    target_day: str,
    model: str,
    eligible_sources: Iterable[str],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Return newest valid sample per source, then sorted cost candidates."""
    if not isinstance(ledger, dict) or not isinstance(ledger.get("days"), dict):
        raise ValueError("billing ledger days must be a JSON object")
    dates = _window_dates(target_day, lookback_days)
    days = ledger["days"]
    kinds: set[str] = set()
    text_input: list[tuple[float, str, str]] = []
    text_output: list[tuple[float, str, str]] = []
    fixed: list[tuple[float, str, str]] = []

    eligible = []
    for slug in sorted(set(eligible_sources)):
        target_entry = (days.get(target_day) or {}).get(slug)
        if _collection_complete(target_entry):
            eligible.append(slug)

    samples: list[tuple[str, str, tuple[str, float, float | None]]] = []
    for slug in eligible:
        target_entry = (days.get(target_day) or {}).get(slug) or {}
        valid = _valid_cost((target_entry.get("per_model_real_cost") or {}).get(model))
        if valid is not None:
            samples.append((slug, target_day, valid))

    # Recent history is only a fallback when no source has a current-day sample.
    if not samples:
        for slug in eligible:
            for sample_day in dates[1:]:
                entry = (days.get(sample_day) or {}).get(slug)
                if not _collection_complete(entry):
                    continue
                info = (entry.get("per_model_real_cost") or {}).get(model)
                valid = _valid_cost(info)
                if valid is None:
                    continue
                samples.append((slug, sample_day, valid))
                break

    for slug, sample_day, valid in samples:
        kind, first, second = valid
        kinds.add(kind)
        if kind == "text":
            text_input.append((first, slug, sample_day))
            text_output.append((float(second), slug, sample_day))
        else:
            fixed.append((first, slug, sample_day))

    return {
        "kinds": kinds,
        "text_input": sorted(text_input, reverse=True),
        "text_output": sorted(text_output, reverse=True),
        "fixed": sorted(fixed, reverse=True),
        "lookback_days": lookback_days,
    }
