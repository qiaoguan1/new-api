#!/usr/bin/env python3
"""Select recent model-level actual deduction samples without guessing costs."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable


DEFAULT_LOOKBACK_DAYS = 7


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


def _valid_cost(info: Any) -> tuple[str, float, float | None] | None:
    if not isinstance(info, dict):
        return None
    kind = info.get("kind")
    if kind == "text":
        input_cost = info.get("input_cost_cny_per_m")
        output_cost = info.get("output_cost_cny_per_m")
        if _positive_number(input_cost) and _positive_number(output_cost):
            return "text", float(input_cost), float(output_cost)
        return None
    if kind in {"fixed", "image", "video"}:
        for key in ("cost_cny_per_call", "cost_cny_per_image"):
            value = info.get(key)
            if _positive_number(value):
                return "fixed", float(value), None
    return None


def _window_dates(target_day: str, lookback_days: int) -> list[str]:
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool):
        raise ValueError("lookback_days must be an integer")
    if lookback_days < 1 or lookback_days > 31:
        raise ValueError("lookback_days must be between 1 and 31")
    target = dt.date.fromisoformat(target_day)
    return [(target - dt.timedelta(days=offset)).isoformat() for offset in range(lookback_days)]


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
        if not _collection_complete(target_entry):
            continue
        eligible.append(slug)

    samples: list[tuple[str, str, tuple[str, float, float | None]]] = []
    for slug in eligible:
        target_entry = (days.get(target_day) or {}).get(slug) or {}
        valid = _valid_cost((target_entry.get("per_model_real_cost") or {}).get(model))
        if valid is not None:
            samples.append((slug, target_day, valid))

    # A recent sample is a model-level fallback, not a way for a stale source
    # to override another source's current-day actual deduction.
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
