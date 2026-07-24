#!/usr/bin/env python3
"""Pure health aggregation policy for the production Channel Monitor."""

from __future__ import annotations

from typing import Any, Iterable


TEST_FRESH_SECONDS = 2 * 60 * 60
BALANCE_FRESH_SECONDS = 24 * 60 * 60
MIN_ERROR_CALLS = 10
MIN_ERROR_COUNT = 5
ERROR_RATE_THRESHOLD = 0.20
SLOW_RESPONSE_MS = 5_000


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fresh(timestamp: Any, now_epoch: int, max_age: int) -> bool:
    value = _integer(timestamp)
    return value > 0 and 0 <= now_epoch - value <= max_age


def summarize_enabled_channels(
    channels: Iterable[dict[str, Any]], now_epoch: int
) -> dict[str, Any]:
    """Return current metrics using enabled channels only."""
    enabled = [channel for channel in channels if channel.get("status") == 1]
    calls = sum(_integer(channel.get("calls_24h")) for channel in enabled)
    success = sum(_integer(channel.get("success_24h")) for channel in enabled)
    errors = sum(_integer(channel.get("errors_24h")) for channel in enabled)
    responses = [
        _integer(channel.get("response_time"))
        for channel in enabled
        if _integer(channel.get("response_time")) > 0
        and _fresh(channel.get("test_time"), now_epoch, TEST_FRESH_SECONDS)
    ]
    fresh_balances = [
        float(channel["balance"])
        for channel in enabled
        if channel.get("balance") is not None
        and _fresh(channel.get("balance_updated_time"), now_epoch, BALANCE_FRESH_SECONDS)
    ]
    last_test_at = max((_integer(channel.get("test_time")) for channel in enabled), default=0)
    last_call_at = max((_integer(channel.get("last_call_at")) for channel in enabled), default=0)
    test_stale = not _fresh(last_test_at, now_epoch, TEST_FRESH_SECONDS)
    error_channels = [channel for channel in enabled if channel.get("last_error")]
    latest_error = max(
        error_channels,
        key=lambda channel: _integer(channel.get("last_error_at")),
        default=None,
    )

    return {
        "enabled_channels": len(enabled),
        "calls_24h": calls,
        "success_24h": success,
        "errors_24h": errors,
        "error_rate_24h": round(errors / calls, 4) if calls else 0,
        "quota_24h": sum(_integer(channel.get("quota_24h")) for channel in enabled),
        "quota_7d": sum(_integer(channel.get("quota_7d")) for channel in enabled),
        "used_quota": sum(_integer(channel.get("used_quota")) for channel in enabled),
        "prompt_tokens_24h": sum(
            _integer(channel.get("prompt_tokens_24h")) for channel in enabled
        ),
        "completion_tokens_24h": sum(
            _integer(channel.get("completion_tokens_24h")) for channel in enabled
        ),
        "db_balance": round(sum(fresh_balances), 6) if fresh_balances else None,
        "avg_response_ms": round(sum(responses) / len(responses)) if responses else 0,
        "last_test_at": last_test_at,
        "last_call_at": last_call_at,
        "last_error": latest_error.get("last_error") if latest_error else "",
        "test_stale": test_stale,
        "health_source": "traffic_24h" if calls else ("channel_test" if not test_stale else "none"),
    }


def classify_health(row: dict[str, Any]) -> str:
    """Classify actionable incidents separately from stale observations."""
    if _integer(row.get("enabled_channels")) == 0:
        return "inactive"

    calls = _integer(row.get("calls_24h"))
    errors = _integer(row.get("errors_24h"))
    if (
        calls >= MIN_ERROR_CALLS
        and errors >= MIN_ERROR_COUNT
        and float(row.get("error_rate_24h") or 0) >= ERROR_RATE_THRESHOLD
    ):
        return "error"

    balance = row.get("db_balance")
    if balance is not None and float(balance) < 1:
        return "low_balance"

    if not row.get("test_stale") and _integer(row.get("avg_response_ms")) >= SLOW_RESPONSE_MS:
        return "slow"

    if calls == 0 and row.get("test_stale"):
        return "stale"
    return "ok"


def is_alert(health: str) -> bool:
    return health in {"error", "slow", "low_balance"}


def is_warning(health: str) -> bool:
    return health == "stale"
