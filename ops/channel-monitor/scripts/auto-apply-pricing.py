#!/usr/bin/env python3
"""Apply daily NewAPI prices from trusted model-level upstream billing costs.

The worker has no model allowlist. It discovers models from the matching daily
channel audit and the previous complete Beijing day's upstream billing ledger.
Only the intersection of a healthy enabled channel and a positive model-level
actual-cost sample can change a price.

Pricing contract:

* source cost = recent actual cost, otherwise authenticated catalog cost
* standard/base price = highest retained source cost x 10
* customer group ratio = 0.15
* customer price = actual cost x 1.5
"""

import argparse
import datetime
import json
import math
import os
import pathlib
import subprocess
import sys
import time

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MODULE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(MODULE_ROOT))

from monitor_time import beijing_day_for_epoch, beijing_now, resolve_beijing_business_day
from recent_actual_cost import collect_recent_model_costs
from video_catalog_policy import normalize_model_name, validate_policy


ROOT = pathlib.Path(os.environ.get("CHANNEL_MONITOR_ROOT", "/opt/ai-api-stack/channel-monitor"))
STACK_ROOT = pathlib.Path(os.environ.get("CHANNEL_MONITOR_STACK_ROOT", "/opt/ai-api-stack"))
LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"
AUDIT_PATH = ROOT / "data" / "daily-upstream-audit.json"
CREDENTIALS_PATH = ROOT / "upstream-credentials.json"
LOG_PATH = ROOT / "data" / "auto-pricing-log.json"
BACKUP_DIR = ROOT / "backups" / "pricing"
VIDEO_POLICY_PATH = ROOT / "config" / "video-model-policy.json"
MANUAL_EVIDENCE_PATH = ROOT / "config" / "pricing-evidence-overrides.json"

EXPECTED_GROUP_RATIO = 0.15
BASE_MULTIPLIER = 10.0
MARKUP = BASE_MULTIPLIER * EXPECTED_GROUP_RATIO
MIN_MARKUP = 1.2
DEFAULT_MAX_CHANGE_RATIO = 5.0
ACTUAL_COST_LOOKBACK_DAYS = 7
MAX_TEXT_COST_CNY_PER_M = 100_000.0
MAX_FIXED_COST_CNY_PER_CALL = 10_000.0
MAX_COMPLETION_RATIO = 1_000.0
MAX_MANUAL_EVIDENCE_DAYS = 31
RECOVERABLE_UNDERPRICING_ALERT_TYPES = frozenset(
    {
        "price_below_upstream_input",
        "price_below_actual_input",
        "price_below_actual_output",
    }
)
DB_USER = os.environ.get("CHANNEL_MONITOR_DB_USER", "newapi")
DB_NAME = os.environ.get("CHANNEL_MONITOR_DB_NAME", "new-api")
OPTION_KEYS = ("ModelRatio", "CompletionRatio", "ModelPrice")
VIDEO_MODEL_MARKERS = (
    "video",
    "seedance",
    "sora",
    "veo",
    "kling",
    "hailuo",
    "minimax-video",
    "grok-video",
    "vidu",
    "wan-video",
)


class PricingError(RuntimeError):
    """Raised when safe automatic pricing cannot continue."""


def read_json(path, default=None, *, required=False):
    """Read a JSON file and fail closed when a required artifact is invalid."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        if required:
            raise PricingError(f"cannot read required JSON {path}: {exc}") from exc
        return default


def write_json(path, value):
    """Atomically write JSON without exposing it through a partially written file."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(str(path) + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, path)


def target_beijing_day():
    """Return the previous complete Beijing day, with an override for replay."""
    return resolve_beijing_business_day(os.environ.get("CHANNEL_MONITOR_DAY", ""))


def _psql_command(sql):
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-t",
        "-A",
        "-c",
        sql,
    ]


def _run_psql(sql):
    try:
        completed = subprocess.run(
            _psql_command(sql),
            cwd=str(STACK_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PricingError(f"psql execution failed: {exc}") from exc
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "unknown psql error").strip()
        raise PricingError(f"psql returned {completed.returncode}: {error}")
    return completed.stdout.strip()


def get_option(key):
    """Fetch one pricing option as a JSON object."""
    if key not in OPTION_KEYS + ("GroupRatio",):
        raise PricingError(f"unsupported option key: {key}")
    output = _run_psql(f"SELECT value FROM options WHERE key='{key}';")
    if not output:
        raise PricingError(f"option {key} is missing")
    try:
        value = json.loads(output)
    except ValueError as exc:
        raise PricingError(f"option {key} contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise PricingError(f"option {key} must be a JSON object")
    return value


def _sql_string(value):
    return value.replace("'", "''")


def atomic_update_options(options, expected_options):
    """Update all pricing option objects in one checked PostgreSQL transaction."""
    if set(options) != set(OPTION_KEYS) or set(expected_options) != set(OPTION_KEYS):
        raise PricingError("atomic update and expected values require all pricing options")
    statements = [
        "BEGIN;",
        "SELECT pg_advisory_xact_lock(hashtext('channel-monitor-auto-pricing'));",
        "DO $pricing$",
        "DECLARE affected_rows integer;",
        "BEGIN",
    ]
    for key in OPTION_KEYS:
        payload = json.dumps(options[key], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        expected = json.dumps(
            expected_options[key], ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        statements.append(
            f"UPDATE options SET value='{_sql_string(payload)}' "
            f"WHERE key='{key}' AND value::jsonb='{_sql_string(expected)}'::jsonb;"
        )
        statements.append("GET DIAGNOSTICS affected_rows = ROW_COUNT;")
        statements.append(
            f"IF affected_rows <> 1 THEN RAISE EXCEPTION 'expected one {key} row, got %', affected_rows; END IF;"
        )
    statements.extend(("END", "$pricing$;", "COMMIT;"))
    output = _run_psql("\n".join(statements))
    if "DO" not in output or "COMMIT" not in output:
        raise PricingError(f"pricing transaction did not update all options: {output!r}")
    return output


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _model_names(channel):
    configured = channel.get("configured_models")
    if isinstance(configured, list):
        return {
            name.strip()
            for name in configured
            if isinstance(name, str) and name.strip()
        }
    models = channel.get("models") or {}
    if not isinstance(models, dict):
        return set()
    return {
        name
        for name, detail in models.items()
        if isinstance(name, str)
        and name.strip()
        and isinstance(detail, dict)
        and detail.get("available") is not False
    }


def build_audit_policy(daily_audit, day):
    """Return discovered models and model-specific eligible upstream sources."""
    if not isinstance(daily_audit, dict) or daily_audit.get("date") != day:
        observed = daily_audit.get("date") if isinstance(daily_audit, dict) else None
        raise PricingError(f"daily audit is stale: expected {day}, got {observed}")

    blocked_channels = set()
    blocked_models = set()
    underpricing_alerts = {}
    for alert in daily_audit.get("alerts") or []:
        if not isinstance(alert, dict) or alert.get("severity") != "critical":
            continue
        channel_id = alert.get("channel_id")
        model = alert.get("model")
        if channel_id is None and not model:
            raise PricingError(f"global critical audit alert: {alert.get('type', 'unknown')}")
        if isinstance(model, str) and model:
            alert_type = alert.get("type")
            if (
                isinstance(alert_type, str)
                and alert_type in RECOVERABLE_UNDERPRICING_ALERT_TYPES
            ):
                underpricing_alerts.setdefault(model, set()).add(alert_type)
            else:
                blocked_models.add(model)
        elif channel_id is not None:
            blocked_channels.add(channel_id)

    discovered_models = set()
    healthy_sources = set()
    model_sources = {}
    model_expected_sources = {}
    for channel in daily_audit.get("channels") or []:
        if not isinstance(channel, dict) or channel.get("status") != 1:
            continue
        models = _model_names(channel)
        discovered_models.update(models)
        slug = channel.get("upstream_slug")
        if isinstance(slug, str) and slug:
            for model in models:
                model_expected_sources.setdefault(model, set()).add(slug)
        if (
            not isinstance(slug, str)
            or not slug
            or channel.get("channel_id") in blocked_channels
            or channel.get("scan_status") != "ok"
        ):
            continue
        healthy_sources.add(slug)
        for model in models:
            if model not in blocked_models:
                model_sources.setdefault(model, set()).add(slug)

    return {
        "discovered_models": discovered_models,
        "healthy_sources": healthy_sources,
        "model_sources": model_sources,
        "model_expected_sources": model_expected_sources,
        "blocked_models": blocked_models,
        "blocked_channels": blocked_channels,
        "underpricing_alerts": underpricing_alerts,
    }


def _validate_group_ratios(group_ratios):
    if not isinstance(group_ratios, dict) or not group_ratios:
        raise PricingError("GroupRatio is missing or empty")
    mismatches = {}
    for group, value in group_ratios.items():
        if not _positive_number(value) or not math.isclose(
            float(value), EXPECTED_GROUP_RATIO, rel_tol=0.0, abs_tol=1e-9
        ):
            mismatches[group] = value
    if mismatches:
        raise PricingError(f"GroupRatio must be {EXPECTED_GROUP_RATIO}: {mismatches}")


def _cost_kind(info):
    if not isinstance(info, dict):
        return None
    if info.get("kind") == "text":
        return "text"
    if info.get("kind") in ("image", "fixed"):
        return "fixed"
    return None


def _fixed_cost(info):
    for key in ("cost_cny_per_call", "cost_cny_per_image"):
        value = info.get(key) if isinstance(info, dict) else None
        if _positive_number(value):
            return float(value)
    return None


def _change_exceeds_limit(current, proposed, max_change_ratio):
    if max_change_ratio is None or max_change_ratio < 0:
        return False
    if not _positive_number(current):
        return False
    return abs(float(proposed) - float(current)) / float(current) > max_change_ratio


def _ledger_day(ledger, day):
    if not isinstance(ledger, dict):
        raise PricingError("billing ledger must be a JSON object")
    day_rows = (ledger.get("days") or {}).get(day)
    if not isinstance(day_rows, dict):
        raise PricingError(f"billing ledger has no complete day {day}")
    return day_rows


def _collection_complete(entry):
    """Only an explicitly successful dated billing-log query is trustworthy."""
    return (
        isinstance(entry, dict)
        and entry.get("collection_status") == "complete"
        and entry.get("actual_log_complete") is True
    )


def incomplete_credential_sources(ledger, day, credentials):
    """List configured accounts without a complete dated billing collection."""
    day_rows = _ledger_day(ledger, day)
    if not isinstance(credentials, dict):
        raise PricingError("upstream credentials must be a JSON object")
    return sorted(
        slug
        for slug, value in credentials.items()
        if isinstance(value, dict) and not _collection_complete(day_rows.get(slug))
    )


def _collect_model_costs(day_rows, model, eligible_sources):
    text_input = []
    text_output = []
    fixed = []
    observed_kinds = set()
    for slug in sorted(eligible_sources):
        entry = day_rows.get(slug) or {}
        info = ((entry.get("per_model_real_cost") or {}).get(model))
        kind = _cost_kind(info)
        if kind is None:
            continue
        observed_kinds.add(kind)
        if kind == "text":
            input_cost = info.get("input_cost_cny_per_m")
            output_cost = info.get("output_cost_cny_per_m")
            if _positive_number(input_cost):
                text_input.append((float(input_cost), slug))
            if _positive_number(output_cost):
                text_output.append((float(output_cost), slug))
        else:
            cost = _fixed_cost(info)
            if cost is not None:
                fixed.append((cost, slug))
    return {
        "kinds": observed_kinds,
        "text_input": sorted(text_input, reverse=True),
        "text_output": sorted(text_output, reverse=True),
        "fixed": sorted(fixed, reverse=True),
    }


def _base_decision(model):
    return {"model": model, "action": "skip", "reason": "no_trusted_actual_cost"}


def _catalog_metadata_is_current(metadata, day):
    """Accept catalog metadata captured for the business day or next morning."""
    if not isinstance(metadata, dict) or metadata.get("status") != "complete":
        return False
    fetched_at = metadata.get("fetched_at")
    if (
        not isinstance(fetched_at, (int, float))
        or isinstance(fetched_at, bool)
        or not math.isfinite(fetched_at)
        or fetched_at <= 0
    ):
        return False
    try:
        metadata_day = beijing_day_for_epoch(fetched_at)
        target = datetime.date.fromisoformat(day)
    except (TypeError, ValueError, OSError, OverflowError):
        return False
    return metadata_day in {day, (target + datetime.timedelta(days=1)).isoformat()}


def _catalog_cost(entry, model, day):
    """Normalize one authenticated NewAPI catalog row to CNY cost units."""
    if not isinstance(entry, dict):
        return None
    metadata = entry.get("pricing_metadata")
    if not _catalog_metadata_is_current(metadata, day):
        return None
    account_models = metadata.get("account_models")
    if not isinstance(account_models, list) or model not in account_models:
        return None
    raw_rows = metadata.get("models")
    if not isinstance(raw_rows, list):
        return None
    rows = [
        row
        for row in raw_rows
        if isinstance(row, dict) and str(row.get("model_name") or "").strip() == model
    ]
    if len(rows) != 1:
        return None
    group = str(entry.get("group") or "").strip()
    group_ratios = metadata.get("group_ratio")
    rate = entry.get("rate")
    if (
        not group
        or not isinstance(group_ratios, dict)
        or not _positive_number(group_ratios.get(group))
        or not _positive_number(rate)
    ):
        return None
    group_ratio = float(group_ratios[group])
    cny_rate = float(rate)
    row = rows[0]
    model_ratio = row.get("model_ratio")
    completion_ratio = row.get("completion_ratio")
    model_price = row.get("model_price")
    billing_mode = str(row.get("billing_mode") or "").strip().lower()
    quota_type = row.get("quota_type")
    if isinstance(quota_type, bool) or quota_type not in {None, 0, 1}:
        return None
    per_second_modes = {"per_sec", "per_second", "second"}
    fixed_mode = (
        (quota_type == 1 or billing_mode in {"fixed", "per_call", "task"})
        and billing_mode not in per_second_modes
    )
    has_fixed = _positive_number(model_price) and fixed_mode
    has_text = (
        _positive_number(model_ratio)
        and _positive_number(completion_ratio)
        and float(completion_ratio) <= MAX_COMPLETION_RATIO
        and not _positive_number(model_price)
        and quota_type != 1
        and billing_mode in {"", "ratio", "token", "per_token"}
    )
    if has_fixed == has_text:
        return None
    fetched_at = int(float(metadata["fetched_at"]))
    if has_fixed:
        fixed_cost = float(model_price) * group_ratio * cny_rate
        if not _positive_number(fixed_cost) or fixed_cost > MAX_FIXED_COST_CNY_PER_CALL:
            return None
        return {
            "kind": "fixed",
            "fixed": fixed_cost,
            "sample_date": day,
            "evidence_type": "authenticated_catalog",
            "fetched_at": fetched_at,
        }
    input_cost = float(model_ratio) * 2.0 * group_ratio * cny_rate
    output_cost = input_cost * float(completion_ratio)
    if (
        not _positive_number(input_cost)
        or not _positive_number(output_cost)
        or input_cost > MAX_TEXT_COST_CNY_PER_M
        or output_cost > MAX_TEXT_COST_CNY_PER_M
    ):
        return None
    return {
        "kind": "text",
        "input": input_cost,
        "output": output_cost,
        "sample_date": day,
        "evidence_type": "authenticated_catalog",
        "fetched_at": fetched_at,
    }


def _source_actual_cost(ledger, day, model, slug):
    """Return the newest task-backed actual cost for one source and model."""
    costs = collect_recent_model_costs(
        ledger,
        day,
        model,
        {slug},
        lookback_days=ACTUAL_COST_LOOKBACK_DAYS,
    )
    if costs["kinds"] == {"text"} and costs["text_input"] and costs["text_output"]:
        input_cost, _, input_day = costs["text_input"][0]
        output_cost, _, output_day = costs["text_output"][0]
        sample_day = max(input_day, output_day)
        if (
            input_cost > MAX_TEXT_COST_CNY_PER_M
            or output_cost > MAX_TEXT_COST_CNY_PER_M
        ):
            return None
        return {
            "kind": "text",
            "input": input_cost,
            "output": output_cost,
            "sample_date": sample_day,
            "evidence_type": "actual",
            "fetched_at": None,
        }
    if costs["kinds"] == {"fixed"} and costs["fixed"]:
        fixed_cost, _, sample_day = costs["fixed"][0]
        if fixed_cost > MAX_FIXED_COST_CNY_PER_CALL:
            return None
        return {
            "kind": "fixed",
            "fixed": fixed_cost,
            "sample_date": sample_day,
            "evidence_type": "actual",
            "fetched_at": None,
        }
    return None


def _manual_catalog_cost(manual_evidence, slug, model, day):
    """Return bounded, time-limited admin-verified catalog evidence."""
    if (
        not isinstance(manual_evidence, dict)
        or isinstance(manual_evidence.get("version"), bool)
        or manual_evidence.get("version") != 1
    ):
        return None
    sources = manual_evidence.get("sources")
    source = sources.get(slug) if isinstance(sources, dict) else None
    models = source.get("models") if isinstance(source, dict) else None
    row = models.get(model) if isinstance(models, dict) else None
    if not isinstance(row, dict):
        return None
    try:
        target_day = datetime.date.fromisoformat(day)
        verified_on = datetime.date.fromisoformat(row["verified_on"])
        valid_through = datetime.date.fromisoformat(row["valid_through"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        verified_on > target_day
        or target_day > valid_through
        or valid_through < verified_on
        or (valid_through - verified_on).days > MAX_MANUAL_EVIDENCE_DAYS
    ):
        return None
    fetched_at = int(
        datetime.datetime.combine(
            verified_on,
            datetime.time.min,
            tzinfo=datetime.timezone(datetime.timedelta(hours=8)),
        ).timestamp()
    )
    if row.get("kind") == "text":
        input_cost = row.get("input_cost_cny_per_m")
        output_cost = row.get("output_cost_cny_per_m")
        if (
            not _positive_number(input_cost)
            or not _positive_number(output_cost)
            or float(input_cost) > MAX_TEXT_COST_CNY_PER_M
            or float(output_cost) > MAX_TEXT_COST_CNY_PER_M
        ):
            return None
        return {
            "kind": "text",
            "input": float(input_cost),
            "output": float(output_cost),
            "sample_date": row["verified_on"],
            "evidence_type": "manual_authenticated_catalog",
            "fetched_at": fetched_at,
        }
    if row.get("kind") == "fixed":
        fixed_cost = row.get("cost_cny_per_call")
        if (
            not _positive_number(fixed_cost)
            or float(fixed_cost) > MAX_FIXED_COST_CNY_PER_CALL
        ):
            return None
        return {
            "kind": "fixed",
            "fixed": float(fixed_cost),
            "sample_date": row["verified_on"],
            "evidence_type": "manual_authenticated_catalog",
            "fetched_at": fetched_at,
        }
    return None


def _collect_model_evidence(
    ledger,
    day_rows,
    day,
    model,
    eligible_sources,
    manual_evidence=None,
):
    """Collect one best evidence row per source and return cross-source maxima.

    A recent task-backed actual cost is authoritative for that source. The
    authenticated catalog is a fallback only when no recent actual sample is
    available, so a displayed list price cannot replace what the account was
    demonstrably charged.
    """
    kinds = set()
    text_input = []
    text_output = []
    fixed = []
    missing = []
    incomplete = []
    evidence_types = set()
    for slug in sorted(eligible_sources):
        entry = day_rows.get(slug) or {}
        actual = _source_actual_cost(ledger, day, model, slug)
        evidence_rows = [actual] if actual is not None else []
        if not evidence_rows:
            catalog = _catalog_cost(entry, model, day)
            if catalog is not None:
                evidence_rows.append(catalog)
        if not evidence_rows:
            manual_catalog = _manual_catalog_cost(
                manual_evidence, slug, model, day
            )
            if manual_catalog is not None:
                evidence_rows.append(manual_catalog)
        if not evidence_rows:
            missing.append(slug)
            metadata = entry.get("pricing_metadata") if isinstance(entry, dict) else None
            if not _collection_complete(entry) and not (
                isinstance(metadata, dict) and metadata.get("status") == "complete"
            ):
                incomplete.append(slug)
            continue
        for evidence in evidence_rows:
            kind = evidence["kind"]
            kinds.add(kind)
            evidence_types.add(evidence["evidence_type"])
            common = (
                slug,
                evidence["sample_date"],
                evidence["evidence_type"],
                evidence["fetched_at"],
            )
            if kind == "text":
                text_input.append((float(evidence["input"]), *common))
                text_output.append((float(evidence["output"]), *common))
            else:
                fixed.append((float(evidence["fixed"]), *common))
    return {
        "kinds": kinds,
        "text_input": sorted(text_input, key=lambda row: row[0], reverse=True),
        "text_output": sorted(text_output, key=lambda row: row[0], reverse=True),
        "fixed": sorted(fixed, key=lambda row: row[0], reverse=True),
        "missing_sources": missing,
        "incomplete_sources": incomplete,
        "evidence_types": evidence_types,
    }


def _cost_basis(evidence_types, sample_dates, day):
    catalog_types = {
        "authenticated_catalog",
        "manual_authenticated_catalog",
    }
    if evidence_types and evidence_types <= catalog_types:
        return "authenticated_catalog"
    if evidence_types & catalog_types:
        return "mixed_actual_catalog"
    return "current_day_actual" if all(value == day for value in sample_dates) else "recent_actual"


def is_video_model(model):
    """Keep every recognized video SKU out of generic upstream-cost pricing."""
    value = str(model or "").strip().lower()
    return value.startswith(("sd2-", "sd3-", "sd4-")) or any(
        marker in value for marker in VIDEO_MODEL_MARKERS
    )


def protected_video_models(daily_audit, raw_policy):
    """Classify reviewed protocol aliases from policy, not naming heuristics."""
    try:
        policy = validate_policy(raw_policy)
    except (TypeError, ValueError) as exc:
        raise PricingError(f"invalid video model policy: {exc}") from exc
    protected = set()
    for channel in daily_audit.get("channels") or [] if isinstance(daily_audit, dict) else []:
        if not isinstance(channel, dict):
            continue
        source = channel.get("upstream_slug")
        for model in _model_names(channel):
            if normalize_model_name(source, model, policy).get("status") == "matched":
                protected.add(model)
    return protected


def build_pricing_plan(
    ledger,
    daily_audit,
    day,
    current_options,
    *,
    max_change_ratio,
    protected_videos=(),
    manual_evidence=None,
):
    """Build a pure, side-effect-free pricing plan for every discovered model."""
    for key in OPTION_KEYS + ("GroupRatio",):
        if not isinstance(current_options.get(key), dict):
            raise PricingError(f"current option {key} must be a JSON object")
    if (
        not isinstance(max_change_ratio, (int, float))
        or isinstance(max_change_ratio, bool)
        or not math.isfinite(max_change_ratio)
        or max_change_ratio < 0
    ):
        raise PricingError("max change ratio must be a finite non-negative number")
    _validate_group_ratios(current_options["GroupRatio"])
    policy = build_audit_policy(daily_audit, day)
    day_rows = _ledger_day(ledger, day)

    # Current healthy channel configuration defines inventory. Old billing
    # history may price configured models but never resurrect retired models.
    discovered = set(policy["discovered_models"])

    new_model_ratio = dict(current_options["ModelRatio"])
    new_completion_ratio = dict(current_options["CompletionRatio"])
    new_model_price = dict(current_options["ModelPrice"])
    decisions = []

    for model in sorted(discovered):
        decision = _base_decision(model)
        underpricing_alert_types = sorted(policy["underpricing_alerts"].get(model, set()))
        if underpricing_alert_types:
            decision["underpricing_alert_types"] = underpricing_alert_types
        if model in protected_videos or is_video_model(model):
            decision["reason"] = "video_official_pricing_only"
            decisions.append(decision)
            continue
        if model in policy["blocked_models"]:
            decision["reason"] = "critical_model_alert"
            decisions.append(decision)
            continue
        eligible_sources = policy["model_sources"].get(model, set())
        if not eligible_sources:
            decision["reason"] = "no_healthy_enabled_channel"
            decisions.append(decision)
            continue
        expected_sources = policy["model_expected_sources"].get(model, set())
        failed_incomplete_sources = sorted(
            slug
            for slug in expected_sources - eligible_sources
            if not _collection_complete(day_rows.get(slug))
        )
        if failed_incomplete_sources:
            decision["reason"] = "upstream_collection_incomplete"
            decision["incomplete_sources"] = failed_incomplete_sources
            decision["missing_cost_sources"] = failed_incomplete_sources
            decisions.append(decision)
            continue

        costs = _collect_model_evidence(
            ledger,
            day_rows,
            day,
            model,
            eligible_sources,
            manual_evidence,
        )
        if costs["missing_sources"]:
            decision["reason"] = (
                "upstream_collection_incomplete"
                if costs["incomplete_sources"]
                else "no_trusted_cost_evidence"
            )
            decision["missing_cost_sources"] = costs["missing_sources"]
            if costs["incomplete_sources"]:
                decision["incomplete_sources"] = costs["incomplete_sources"]
            decisions.append(decision)
            continue
        if len(costs["kinds"]) > 1:
            decision["reason"] = "ambiguous_billing_kind"
            decisions.append(decision)
            continue

        if costs["kinds"] == {"text"} and costs["text_input"] and costs["text_output"]:
            (
                worst_input,
                input_source,
                input_sample_date,
                input_evidence_type,
                input_catalog_fetched_at,
            ) = costs["text_input"][0]
            (
                worst_output,
                output_source,
                output_sample_date,
                output_evidence_type,
                output_catalog_fetched_at,
            ) = costs["text_output"][0]
            cost_basis = _cost_basis(
                costs["evidence_types"],
                (input_sample_date, output_sample_date),
                day,
            )
            new_ratio = worst_input * BASE_MULTIPLIER / 2.0
            new_completion = worst_output / worst_input
            input_sell = worst_input * MARKUP
            output_sell = worst_output * MARKUP
            if input_sell < worst_input * MIN_MARKUP or output_sell < worst_output * MIN_MARKUP:
                decision["reason"] = "minimum_markup"
            elif _change_exceeds_limit(
                new_model_ratio.get(model), new_ratio, max_change_ratio
            ) or _change_exceeds_limit(
                new_completion_ratio.get(model), new_completion, max_change_ratio
            ):
                decision["reason"] = "price_change_limit"
            else:
                new_model_ratio[model] = round(new_ratio, 12)
                new_completion_ratio[model] = round(new_completion, 12)
                new_model_price.pop(model, None)
                decision.update(
                    {
                        "action": "apply",
                        "reason": (
                            "underpriced_self_correction"
                            if underpricing_alert_types
                            else "ok"
                        ),
                        "billing_kind": "text",
                        "old_model_ratio": current_options["ModelRatio"].get(model),
                        "old_completion_ratio": current_options["CompletionRatio"].get(model),
                        "worst_input_cost_cny_per_m": worst_input,
                        "worst_input_source": input_source,
                        "worst_input_sample_date": input_sample_date,
                        "worst_input_evidence_type": input_evidence_type,
                        "worst_output_cost_cny_per_m": worst_output,
                        "worst_output_source": output_source,
                        "worst_output_sample_date": output_sample_date,
                        "worst_output_evidence_type": output_evidence_type,
                        "cost_basis": cost_basis,
                        "actual_cost_lookback_days": ACTUAL_COST_LOOKBACK_DAYS,
                        "new_model_ratio": round(new_ratio, 12),
                        "new_completion_ratio": round(new_completion, 12),
                        "input_sell_cny_per_m": round(input_sell, 12),
                        "output_sell_cny_per_m": round(output_sell, 12),
                    }
                )
                if input_catalog_fetched_at is not None:
                    decision["worst_input_catalog_fetched_at"] = input_catalog_fetched_at
                if output_catalog_fetched_at is not None:
                    decision["worst_output_catalog_fetched_at"] = output_catalog_fetched_at
        elif costs["kinds"] == {"fixed"} and costs["fixed"]:
            worst_cost, source, sample_date, evidence_type, catalog_fetched_at = costs["fixed"][0]
            cost_basis = _cost_basis(costs["evidence_types"], (sample_date,), day)
            new_price = worst_cost * BASE_MULTIPLIER
            sell = worst_cost * MARKUP
            if sell < worst_cost * MIN_MARKUP:
                decision["reason"] = "minimum_markup"
            elif _change_exceeds_limit(
                new_model_price.get(model), new_price, max_change_ratio
            ):
                decision["reason"] = "price_change_limit"
            else:
                new_model_price[model] = round(new_price, 12)
                new_model_ratio.pop(model, None)
                new_completion_ratio.pop(model, None)
                decision.update(
                    {
                        "action": "apply",
                        "reason": (
                            "underpriced_self_correction"
                            if underpricing_alert_types
                            else "ok"
                        ),
                        "billing_kind": "fixed",
                        "old_model_price": current_options["ModelPrice"].get(model),
                        "worst_cost_cny_per_call": worst_cost,
                        "worst_source": source,
                        "worst_cost_sample_date": sample_date,
                        "worst_cost_evidence_type": evidence_type,
                        "cost_basis": cost_basis,
                        "actual_cost_lookback_days": ACTUAL_COST_LOOKBACK_DAYS,
                        "new_model_price": round(new_price, 12),
                        "sell_cny_per_call": round(sell, 12),
                    }
                )
                if catalog_fetched_at is not None:
                    decision["worst_cost_catalog_fetched_at"] = catalog_fetched_at
        decisions.append(decision)

    return {
        "date": day,
        "group_ratio": EXPECTED_GROUP_RATIO,
        "markup": MARKUP,
        "decisions": decisions,
        "options": {
            "ModelRatio": new_model_ratio,
            "CompletionRatio": new_completion_ratio,
            "ModelPrice": new_model_price,
        },
    }


def backup_pricing_options(day, options):
    """Create a mode-0600 pricing-only backup before a live transaction."""
    timestamp = beijing_now().strftime("%Y%m%dT%H%M%S%z")
    path = BACKUP_DIR / f"pricing-options-{day}-{timestamp}.json"
    write_json(path, {"date": day, "created_at": timestamp, "options": options})
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise PricingError(f"cannot secure pricing backup {path}: {exc}") from exc
    return path


def append_run_log(run):
    history = read_json(LOG_PATH, {"runs": []})
    if not isinstance(history, dict):
        history = {"runs": []}
    runs = history.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(run)
    history["runs"] = runs[-90:]
    write_json(LOG_PATH, history)


def _summary(plan, dry_run):
    decisions = plan.get("decisions") or []
    return {
        "date": plan.get("date"),
        "dry_run": dry_run,
        "discovered_models": len(decisions),
        "applied_models": sum(1 for item in decisions if item.get("action") == "apply"),
        "skipped_models": sum(1 for item in decisions if item.get("action") != "apply"),
        "decisions": [
            {
                key: item.get(key)
                for key in (
                    "model",
                    "billing_kind",
                    "action",
                    "reason",
                    "underpricing_alert_types",
                    "incomplete_sources",
                    "missing_cost_sources",
                    "old_model_ratio",
                    "old_completion_ratio",
                    "old_model_price",
                    "worst_input_cost_cny_per_m",
                    "worst_input_source",
                    "worst_input_sample_date",
                    "worst_input_evidence_type",
                    "worst_input_catalog_fetched_at",
                    "worst_output_cost_cny_per_m",
                    "worst_output_source",
                    "worst_output_sample_date",
                    "worst_output_evidence_type",
                    "worst_output_catalog_fetched_at",
                    "worst_cost_cny_per_call",
                    "worst_source",
                    "worst_cost_sample_date",
                    "worst_cost_evidence_type",
                    "worst_cost_catalog_fetched_at",
                    "cost_basis",
                    "actual_cost_lookback_days",
                    "new_model_ratio",
                    "new_completion_ratio",
                    "new_model_price",
                    "input_sell_cny_per_m",
                    "output_sell_cny_per_m",
                    "sell_cny_per_call",
                )
                if item.get(key) is not None
            }
            for item in decisions
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="calculate without database writes")
    args = parser.parse_args(argv)
    day = target_beijing_day()
    generated_at = int(time.time())
    try:
        ledger = read_json(LEDGER_PATH, required=True)
        daily_audit = read_json(AUDIT_PATH, required=True)
        video_policy = read_json(VIDEO_POLICY_PATH, required=True)
        manual_evidence = read_json(
            MANUAL_EVIDENCE_PATH,
            {"version": 1, "sources": {}},
        )
        credentials = read_json(CREDENTIALS_PATH, required=True)
        incomplete_credentials = incomplete_credential_sources(ledger, day, credentials)
        current = {key: get_option(key) for key in OPTION_KEYS + ("GroupRatio",)}
        max_change_ratio = float(
            os.environ.get("CHANNEL_MONITOR_MAX_CHANGE_RATIO", DEFAULT_MAX_CHANGE_RATIO)
        )
        plan = build_pricing_plan(
            ledger,
            daily_audit,
            day,
            current,
            max_change_ratio=max_change_ratio,
            protected_videos=protected_video_models(daily_audit, video_policy),
            manual_evidence=manual_evidence,
        )
        run = {
            "date": plan["date"],
            "group_ratio": plan["group_ratio"],
            "markup": plan["markup"],
            "decisions": plan["decisions"],
            "dry_run": args.dry_run,
            "generated_at": generated_at,
            "max_change_ratio": max_change_ratio,
            "status": "complete",
            "incomplete_credentials": incomplete_credentials,
        }
        if not args.dry_run and any(
            item.get("action") == "apply" for item in plan["decisions"]
        ):
            backup_path = backup_pricing_options(
                day, {key: current[key] for key in OPTION_KEYS}
            )
            run["backup_path"] = str(backup_path)
            run["database_output"] = atomic_update_options(
                plan["options"], {key: current[key] for key in OPTION_KEYS}
            )
        append_run_log(run)
        print(json.dumps(_summary(plan, args.dry_run), ensure_ascii=False, sort_keys=True))
        return 0
    except (PricingError, ValueError) as exc:
        failure = {
            "date": day,
            "dry_run": args.dry_run,
            "generated_at": generated_at,
            "status": "failed",
            "error": str(exc),
        }
        try:
            append_run_log(failure)
        except Exception:
            pass
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
