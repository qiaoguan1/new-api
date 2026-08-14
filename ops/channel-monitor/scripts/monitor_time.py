"""Authoritative Beijing-time policy for channel monitoring and billing days."""

import datetime
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)


def beijing_now(value=None):
    """Return an aware datetime converted to the fixed business timezone."""
    if value is None:
        return datetime.datetime.now(BUSINESS_TIMEZONE)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("business-time conversion requires a timezone-aware datetime")
    return value.astimezone(BUSINESS_TIMEZONE)


def previous_complete_beijing_day(value=None):
    """Return the previous complete Beijing calendar day as YYYY-MM-DD."""
    return (beijing_now(value).date() - datetime.timedelta(days=1)).isoformat()


def resolve_beijing_business_day(override="", value=None):
    """Validate an override or resolve the previous complete Beijing day."""
    candidate = str(override or "").strip()
    if not candidate:
        return previous_complete_beijing_day(value)
    try:
        parsed = datetime.datetime.strptime(candidate, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("CHANNEL_MONITOR_DAY must be a valid YYYY-MM-DD date") from exc
    if parsed.isoformat() != candidate:
        raise ValueError("CHANNEL_MONITOR_DAY must use canonical YYYY-MM-DD format")
    return candidate


def beijing_day_for_epoch(epoch):
    """Assign a Unix epoch timestamp to its Beijing calendar day."""
    return datetime.datetime.fromtimestamp(int(epoch), BUSINESS_TIMEZONE).date().isoformat()


def beijing_iso(value=None):
    """Return an ISO-8601 timestamp with an explicit +08:00 offset."""
    return beijing_now(value).isoformat(timespec="seconds")


def beijing_iso_now():
    """Return the current Beijing timestamp with an explicit offset."""
    return beijing_iso()
