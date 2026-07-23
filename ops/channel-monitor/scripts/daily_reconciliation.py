"""Pure helpers for same-day upstream billing versus local NewAPI usage."""


def _round(value):
    return round(float(value or 0), 6)


def _safe_div(numerator, denominator):
    denominator = float(denominator or 0)
    if denominator <= 0:
        return None
    return round(float(numerator or 0) / denominator, 4)


def _entry_complete(entry):
    return (
        isinstance(entry, dict)
        and entry.get("collection_status") == "complete"
        and entry.get("actual_log_complete") is True
    )


def build_reconciliation(
    upstreams,
    channels,
    audit,
    ledger,
    day,
    usage_rows,
    credential_slugs,
    channel_matches_upstream,
    quota_to_amount,
):
    """Return one explicit row per configured, credentialed, or audited upstream."""
    configured = {
        item.get("slug"): item
        for item in upstreams or []
        if isinstance(item, dict) and item.get("slug")
    }
    credential_slugs = {slug for slug in credential_slugs or [] if slug}
    day_entries = ((ledger or {}).get("days") or {}).get(day) or {}

    channel_slug = {}
    enabled_by_slug = {}
    channel_count_by_slug = {}
    for channel in channels or []:
        for slug, upstream in configured.items():
            if channel_matches_upstream(channel, upstream):
                channel_id = int(channel.get("id") or 0)
                channel_slug[channel_id] = slug
                channel_count_by_slug[slug] = channel_count_by_slug.get(slug, 0) + 1
                if channel.get("status") == 1:
                    enabled_by_slug[slug] = enabled_by_slug.get(slug, 0) + 1
                break

    audit_slugs = {
        item.get("upstream_slug")
        for item in (audit or {}).get("channels") or []
        if isinstance(item, dict) and item.get("upstream_slug")
    }
    slugs = sorted(set(configured) | credential_slugs | set(day_entries) | audit_slugs)

    local_by_slug = {}
    all_local_calls = 0
    all_local_billed = 0.0
    unassigned_local_calls = 0
    unassigned_local_billed = 0.0
    for item in usage_rows or []:
        calls = int(item.get("calls") or 0)
        billed = quota_to_amount(item.get("quota"))
        all_local_calls += calls
        all_local_billed += billed
        slug = channel_slug.get(int(item.get("channel_id") or 0))
        if not slug:
            unassigned_local_calls += calls
            unassigned_local_billed += billed
            continue
        row = local_by_slug.setdefault(
            slug,
            {"calls": 0, "success_calls": 0, "error_calls": 0, "local_billed_cny": 0.0},
        )
        row["calls"] += calls
        row["success_calls"] += int(item.get("success_calls") or 0)
        row["error_calls"] += int(item.get("error_calls") or 0)
        row["local_billed_cny"] += billed

    rows = []
    for slug in slugs:
        metadata = configured.get(slug) or {}
        entry = day_entries.get(slug)
        complete = _entry_complete(entry)
        has_credentials = slug in credential_slugs
        if complete:
            status = "complete"
        elif isinstance(entry, dict):
            status = entry.get("collection_status") or "incomplete"
        elif has_credentials:
            status = "missing"
        else:
            status = "no_credentials"

        local = local_by_slug.get(slug) or {}
        local_amount = _round(local.get("local_billed_cny"))
        local_calls = int(local.get("calls") or 0)
        enabled_channels = enabled_by_slug.get(slug, 0)
        required_for_reconciliation = bool(
            has_credentials
            or enabled_channels > 0
            or local_calls > 0
            or local_amount > 0
        )
        actual = _round(entry.get("day_log_cost_cny")) if complete else None
        delta = round(local_amount - actual, 6) if actual is not None else None
        row = {
            "slug": slug,
            "name": metadata.get("name") or slug,
            "collection_status": status,
            "actual_log_complete": complete,
            "has_credentials": has_credentials,
            "required_for_reconciliation": required_for_reconciliation,
            "billing_api": (entry or {}).get("billing_api"),
            "collection_error": (entry or {}).get("collection_error")
            or (entry or {}).get("last_attempt_error"),
            "last_attempt_status": (entry or {}).get("last_attempt_status"),
            "fetched_at": (entry or {}).get("fetched_at"),
            "enabled_channels": enabled_channels,
            "channel_count": channel_count_by_slug.get(slug, 0),
            "upstream_log_rows": (entry or {}).get("day_log_rows") if complete else None,
            "upstream_actual_cost_cny": actual,
            "local_calls": local_calls,
            "local_success_calls": int(local.get("success_calls") or 0),
            "local_error_calls": int(local.get("error_calls") or 0),
            "local_billed_cny": local_amount,
            "difference_cny": delta,
            "gross_margin": _safe_div(delta, local_amount) if delta is not None else None,
            "upstream_source": "account_billing_log" if complete else None,
            "local_source": "newapi.logs.quota",
        }
        rows.append(row)

    complete_rows = [row for row in rows if row["actual_log_complete"]]
    incomplete_rows = [row for row in rows if not row["actual_log_complete"]]
    required_rows = [row for row in rows if row["required_for_reconciliation"]]
    complete_required_rows = [row for row in required_rows if row["actual_log_complete"]]
    incomplete_required_rows = [row for row in required_rows if not row["actual_log_complete"]]
    optional_rows = [row for row in rows if not row["required_for_reconciliation"]]
    local_total = _round(all_local_billed)
    actual_total = _round(
        sum(row["upstream_actual_cost_cny"] for row in complete_required_rows)
    )
    unassigned_billable_usage = unassigned_local_billed > 0
    all_complete = not incomplete_required_rows and not unassigned_billable_usage
    difference = round(local_total - actual_total, 6) if all_complete else None
    return {
        "date": day,
        "complete": all_complete,
        "rows": rows,
        "totals": {
            "expected_upstreams": len(rows),
            "complete_upstreams": len(complete_rows),
            "incomplete_upstreams": len(incomplete_rows),
            "required_upstreams": len(required_rows),
            "complete_required_upstreams": len(complete_required_rows),
            "incomplete_required_upstreams": len(incomplete_required_rows),
            "optional_upstreams": len(optional_rows),
            "credentialless_upstreams": sum(1 for row in rows if not row["has_credentials"]),
            "local_billed_cny": local_total,
            "upstream_actual_cost_cny": actual_total,
            "difference_cny": difference,
            "gross_margin": _safe_div(difference, local_total) if difference is not None else None,
            "local_calls": all_local_calls,
            "mapped_local_calls": sum(row["local_calls"] for row in rows),
            "unassigned_local_calls": unassigned_local_calls,
            "unassigned_local_billed_cny": _round(unassigned_local_billed),
            "unassigned_billable_usage": unassigned_billable_usage,
        },
    }
