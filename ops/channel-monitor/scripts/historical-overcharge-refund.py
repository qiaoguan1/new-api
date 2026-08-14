#!/usr/bin/env python3
"""Plan and apply idempotent compensation for historical overcharging.

Only model-level actual costs observed in the upstream billing ledger are used.
The original consumption logs remain unchanged. Live execution credits wallet
quota, reduces lifetime/token usage, and records an immutable source-log keyed
refund audit in one PostgreSQL transaction.
"""

import argparse
import hashlib
import hmac
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(os.environ.get("CHANNEL_MONITOR_ROOT", "/opt/ai-api-stack/channel-monitor"))
STACK_ROOT = pathlib.Path(os.environ.get("CHANNEL_MONITOR_STACK_ROOT", "/opt/ai-api-stack"))
LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"
AUDIT_PATH = ROOT / "data" / "daily-upstream-audit.json"
PLAN_PATH = ROOT / "data" / "historical-overcharge-refund-plan.json"
BACKUP_DIR = ROOT / "backups" / "historical-overcharge-refunds"
DB_USER = os.environ.get("CHANNEL_MONITOR_DB_USER", "newapi")
DB_NAME = os.environ.get("CHANNEL_MONITOR_DB_NAME", "new-api")
QUOTA_PER_UNIT = Decimal("500000")
POLICY_MARKUP = Decimal("1.5")
PLAN_VERSION = 2
ISSUE_NUMBER = 111
BEIJING = ZoneInfo("Asia/Shanghai")
EXCLUDED_SOURCES = frozenset({"packapi", "unity2"})
VIDEO_SETTLEMENT_SOURCES = frozenset({"paisio", "rolldek", "toonflow"})
TRUSTED_FIXED_COST_MODELS = frozenset(
    {("maolao", "gpt-image-2"), ("jojocode", "gpt-image-2")}
)


class RefundError(RuntimeError):
    """Raised when compensation cannot be proved or executed safely."""


def read_json(path, *, required=True):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        if required:
            raise RefundError(f"cannot read JSON {path}: {exc}") from exc
        return None


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _decimal(value, name, *, allow_zero=False):
    if isinstance(value, bool):
        raise RefundError(f"{name} is not numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RefundError(f"{name} is not numeric") from exc
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise RefundError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return result


def _integer(value, name, *, allow_zero=True):
    number = _decimal(value, name, allow_zero=allow_zero)
    integral = number.to_integral_value()
    if number != integral:
        raise RefundError(f"{name} must be an integer")
    return int(integral)


def _normal_model(value):
    return str(value or "").strip().casefold()


def _source_quality(entry):
    if not isinstance(entry, dict):
        return None
    if entry.get("collection_status") == "complete" and entry.get("actual_log_complete") is True:
        return "complete_actual_log"
    completeness_fields_absent = (
        "collection_status" not in entry and "actual_log_complete" not in entry
    )
    if completeness_fields_absent and int(entry.get("day_log_rows") or 0) > 0:
        return "legacy_actual_sample"
    return None


def build_evidence_index(ledger, excluded_sources=EXCLUDED_SOURCES):
    """Index complete actual costs by Beijing day, source and model.

    Historical wallet logs persist the serving channel.  Binding evidence to
    that channel is mandatory: a cheaper or healthier unrelated upstream must
    never be used to increase a customer's refund.
    """
    index = {}
    days = (ledger or {}).get("days") or {}
    for day, sources in sorted(days.items()):
        if not isinstance(sources, dict):
            continue
        for source, entry in sorted(sources.items()):
            normalized_source = _normal_model(source)
            if normalized_source in excluded_sources:
                continue
            quality = _source_quality(entry)
            if quality != "complete_actual_log":
                continue
            costs = entry.get("per_model_real_cost") or {}
            if not isinstance(costs, dict):
                continue
            for display_model, cost in sorted(costs.items()):
                if not isinstance(cost, dict):
                    continue
                try:
                    calls = _integer(cost.get("calls"), "calls", allow_zero=False)
                except RefundError:
                    continue
                if calls <= 0:
                    continue
                kind = cost.get("kind")
                item = {
                    "day": str(day),
                    "model": str(display_model),
                    "source": normalized_source,
                    "quality": quality,
                    "calls": calls,
                }
                try:
                    if kind == "text":
                        item.update(
                            kind="text",
                            input_cost_cny_per_m=float(
                                _decimal(cost.get("input_cost_cny_per_m"), "input cost")
                            ),
                            output_cost_cny_per_m=float(
                                _decimal(cost.get("output_cost_cny_per_m"), "output cost")
                            ),
                        )
                    elif kind in {"fixed", "image"}:
                        if (normalized_source, _normal_model(display_model)) not in TRUSTED_FIXED_COST_MODELS:
                            continue
                        raw_fixed = cost.get("cost_cny_per_call")
                        if raw_fixed is None:
                            raw_fixed = cost.get("cost_cny_per_image")
                        item.update(
                            kind="fixed",
                            cost_cny_per_call=float(_decimal(raw_fixed, "fixed cost")),
                            fixed_cost_contract="verified_uniform_per_call",
                        )
                    else:
                        continue
                except RefundError:
                    continue
                key = (str(day), normalized_source, _normal_model(display_model))
                if key in index:
                    raise RefundError(f"ambiguous actual-cost evidence for {key}")
                index[key] = item
    return index


def build_channel_source_map(audit):
    """Return an unambiguous persisted channel-id to upstream-slug mapping."""
    result = {}
    for row in (audit or {}).get("channels") or []:
        if not isinstance(row, dict):
            continue
        try:
            channel_id = _integer(row.get("channel_id"), "channel id", allow_zero=False)
        except RefundError:
            continue
        source = _normal_model(row.get("upstream_slug"))
        if not source or source in EXCLUDED_SOURCES:
            continue
        previous = result.get(channel_id)
        if previous and previous != source:
            raise RefundError(f"ambiguous upstream mapping for channel {channel_id}")
        result[channel_id] = source
    return result


def _other(log):
    value = log.get("other")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise RefundError("log other is invalid JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise RefundError("log other is missing")


def _round_quota(value):
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def calculate_refund(log, evidence):
    """Return policy and positive refund quota for one consumption log."""
    original_quota = _integer(log.get("quota"), "original quota", allow_zero=True)
    other = _other(log)
    evidence_kind = evidence.get("kind")
    is_task = _truthy(other.get("is_task"))
    try:
        model_price = _decimal(other.get("model_price"), "model price", allow_zero=True)
    except RefundError:
        model_price = Decimal("-1")
    locally_fixed = is_task or model_price >= 0

    if evidence_kind == "fixed":
        if not locally_fixed:
            raise RefundError("fixed actual cost does not match a fixed/task log")
        actual_cost = _decimal(evidence.get("cost_cny_per_call"), "fixed actual cost")
    elif evidence_kind == "text":
        if locally_fixed:
            raise RefundError("text actual cost does not match a fixed/task log")
        prompt = _integer(log.get("prompt_tokens"), "prompt tokens", allow_zero=True)
        completion = _integer(
            log.get("completion_tokens"), "completion tokens", allow_zero=True
        )
        cache = _integer(other.get("cache_tokens", 0), "cache tokens", allow_zero=True)
        creation = _integer(
            other.get("cache_creation_tokens", 0),
            "cache creation tokens",
            allow_zero=True,
        )
        creation_5m = _integer(
            other.get("cache_creation_tokens_5m", 0),
            "5m cache creation tokens",
            allow_zero=True,
        )
        creation_1h = _integer(
            other.get("cache_creation_tokens_1h", 0),
            "1h cache creation tokens",
            allow_zero=True,
        )
        if cache + creation > prompt or creation_5m + creation_1h > creation:
            raise RefundError("cache tokens exceed prompt/cache-creation totals")
        cache_ratio = _decimal(other.get("cache_ratio", 1), "cache ratio", allow_zero=True)
        creation_ratio = _decimal(
            other.get("cache_creation_ratio", 1),
            "cache creation ratio",
            allow_zero=True,
        )
        creation_5m_ratio = _decimal(
            other.get("cache_creation_ratio_5m", creation_ratio),
            "5m cache creation ratio",
            allow_zero=True,
        )
        creation_1h_ratio = _decimal(
            other.get("cache_creation_ratio_1h", creation_ratio),
            "1h cache creation ratio",
            allow_zero=True,
        )
        remaining_creation = creation - creation_5m - creation_1h
        weighted_input = (
            Decimal(prompt - cache - creation)
            + Decimal(cache) * cache_ratio
            + Decimal(remaining_creation) * creation_ratio
            + Decimal(creation_5m) * creation_5m_ratio
            + Decimal(creation_1h) * creation_1h_ratio
        )
        input_cost = _decimal(
            evidence.get("input_cost_cny_per_m"), "actual input cost"
        )
        output_cost = _decimal(
            evidence.get("output_cost_cny_per_m"), "actual output cost"
        )
        actual_cost = (
            weighted_input * input_cost + Decimal(completion) * output_cost
        ) / Decimal("1000000")
    else:
        raise RefundError("unsupported or ambiguous actual-cost kind")

    policy_quota = _round_quota(actual_cost * POLICY_MARKUP * QUOTA_PER_UNIT)
    refund_quota = max(0, original_quota - policy_quota)
    return {
        "original_quota": original_quota,
        "actual_cost_cny": float(actual_cost),
        "policy_quota": policy_quota,
        "refund_quota": refund_quota,
    }


def _fingerprint(log):
    fields = {
        key: log.get(key)
        for key in (
            "id",
            "user_id",
            "token_id",
            "channel_id",
            "created_at",
            "beijing_date",
            "model_name",
            "quota",
            "prompt_tokens",
            "completion_tokens",
            "request_id",
            "other_md5",
            "other",
        )
    }
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _plan_digest(refunds):
    contract = {
        "version": PLAN_VERSION,
        "issue": ISSUE_NUMBER,
        "markup": str(POLICY_MARKUP),
        "quota_per_unit": str(QUOTA_PER_UNIT),
        "refunds": refunds,
    }
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_refund_plan(logs, evidence_index, channel_sources, already_refunded=None):
    already_refunded = {int(item) for item in (already_refunded or set())}
    refunds = []
    skipped = []
    counters = defaultdict(int)
    for log in sorted(logs or [], key=lambda item: int(item.get("id") or 0)):
        source_id = int(log.get("id") or 0)
        if source_id in already_refunded:
            counters["already_refunded"] += 1
            continue
        day = str(log.get("beijing_date") or "")
        model = _normal_model(log.get("model_name"))
        channel_id = int(log.get("channel_id") or 0)
        source = _normal_model(channel_sources.get(channel_id))
        if not source:
            counters["unmapped_channel"] += 1
            skipped.append(
                {
                    "source_log_id": source_id,
                    "user_id": int(log.get("user_id") or 0),
                    "channel_id": channel_id,
                    "beijing_date": day,
                    "model_name": log.get("model_name"),
                    "original_quota": int(log.get("quota") or 0),
                    "reason": "unmapped_channel",
                }
            )
            continue
        if source in VIDEO_SETTLEMENT_SOURCES:
            counters["video_settlement_only"] += 1
            skipped.append(
                {
                    "source_log_id": source_id,
                    "user_id": int(log.get("user_id") or 0),
                    "channel_id": channel_id,
                    "source": source,
                    "beijing_date": day,
                    "model_name": log.get("model_name"),
                    "original_quota": int(log.get("quota") or 0),
                    "reason": "video_settlement_only",
                }
            )
            continue
        evidence = evidence_index.get((day, source, model))
        if not evidence:
            counters["missing_evidence"] += 1
            skipped.append(
                {
                    "source_log_id": source_id,
                    "user_id": int(log.get("user_id") or 0),
                    "channel_id": channel_id,
                    "source": source,
                    "beijing_date": day,
                    "model_name": log.get("model_name"),
                    "original_quota": int(log.get("quota") or 0),
                    "reason": "missing_actual_cost",
                }
            )
            continue
        try:
            calculation = calculate_refund(log, evidence)
        except RefundError as exc:
            counters["invalid_logs"] += 1
            skipped.append(
                {
                    "source_log_id": source_id,
                    "user_id": int(log.get("user_id") or 0),
                    "beijing_date": day,
                    "model_name": log.get("model_name"),
                    "original_quota": int(log.get("quota") or 0),
                    "reason": "invalid_billing_inputs",
                    "detail": str(exc),
                }
            )
            continue
        counters["evaluated_with_evidence"] += 1
        if calculation["refund_quota"] <= 0:
            counters["at_or_below_policy"] += 1
            continue
        refunds.append(
            {
                "source_log_id": source_id,
                "source_fingerprint": _fingerprint(log),
                "user_id": int(log.get("user_id") or 0),
                "username": str(log.get("username") or ""),
                "token_id": int(log.get("token_id") or 0),
                "channel_id": channel_id,
                "source": source,
                "request_id": str(log.get("request_id") or ""),
                "source_created_at": int(log.get("created_at") or 0),
                "source_prompt_tokens": int(log.get("prompt_tokens") or 0),
                "source_completion_tokens": int(log.get("completion_tokens") or 0),
                "source_other_md5": str(log.get("other_md5") or ""),
                "beijing_date": day,
                "model_name": str(log.get("model_name") or ""),
                **calculation,
                "evidence": evidence,
            }
        )

    user_rows = defaultdict(lambda: {"requests": 0, "original_quota": 0, "policy_quota": 0, "refund_quota": 0})
    usernames = {}
    for item in refunds:
        row = user_rows[item["user_id"]]
        row["requests"] += 1
        row["original_quota"] += item["original_quota"]
        row["policy_quota"] += item["policy_quota"]
        row["refund_quota"] += item["refund_quota"]
        usernames[item["user_id"]] = item["username"]
    users = [
        {"user_id": user_id, "username": usernames[user_id], **values}
        for user_id, values in sorted(user_rows.items())
    ]
    totals = {
        "source_logs": len(logs or []),
        "evaluated_with_evidence": counters["evaluated_with_evidence"],
        "refund_requests": len(refunds),
        "affected_users": len(users),
        "refund_quota": sum(item["refund_quota"] for item in refunds),
        "refund_cny": float(Decimal(sum(item["refund_quota"] for item in refunds)) / QUOTA_PER_UNIT),
        "at_or_below_policy": counters["at_or_below_policy"],
        "missing_evidence": counters["missing_evidence"],
        "unmapped_channel": counters["unmapped_channel"],
        "video_settlement_only": counters["video_settlement_only"],
        "invalid_logs": counters["invalid_logs"],
        "already_refunded": counters["already_refunded"],
    }
    return {
        "version": PLAN_VERSION,
        "issue": ISSUE_NUMBER,
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "policy": {
            "markup": float(POLICY_MARKUP),
            "quota_per_unit": int(QUOTA_PER_UNIT),
            "timezone": "Asia/Shanghai",
            "excluded_sources": sorted(EXCLUDED_SOURCES),
            "video_settlement_sources": sorted(VIDEO_SETTLEMENT_SOURCES),
            "evidence_binding": "beijing_date+upstream_slug+normalized_model",
        },
        "refunds": refunds,
        "users": users,
        "skipped": skipped,
        "totals": totals,
        "plan_sha256": _plan_digest(refunds),
    }


def _psql_command():
    return [
        "docker", "compose", "exec", "-T", "postgres", "psql",
        "-v", "ON_ERROR_STOP=1", "-U", DB_USER, "-d", DB_NAME,
        "-t", "-A",
    ]


def _run_psql(sql, *, timeout=120):
    try:
        completed = subprocess.run(
            _psql_command(), cwd=str(STACK_ROOT), capture_output=True, input=sql,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RefundError(f"psql execution failed: {exc}") from exc
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "unknown psql error").strip()
        raise RefundError(f"psql returned {completed.returncode}: {error}")
    return completed.stdout.strip()


def _json_lines(sql):
    output = _run_psql(sql)
    if not output:
        return []
    try:
        return [json.loads(line) for line in output.splitlines() if line.strip()]
    except ValueError as exc:
        raise RefundError("database returned invalid JSON") from exc


def load_consumption_logs():
    sql = """
SELECT json_build_object(
  'id', id, 'user_id', user_id, 'username', username, 'token_id', token_id,
  'channel_id', channel_id,
  'created_at', created_at,
  'beijing_date', to_char(to_timestamp(created_at) AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD'),
  'model_name', model_name, 'quota', quota, 'prompt_tokens', prompt_tokens,
  'completion_tokens', completion_tokens, 'request_id', request_id,
  'other_md5', md5(other),
  'other', other::jsonb
)::text
FROM logs WHERE type=2 ORDER BY id;
"""
    return _json_lines(sql)


def load_existing_refunds():
    exists = _run_psql(
        "SELECT CASE WHEN to_regclass('public.historical_pricing_refunds') IS NULL THEN '0' ELSE '1' END;"
    ).strip()
    if exists == "0":
        return set()
    if exists != "1":
        raise RefundError("cannot determine whether refund audit table exists")
    output = _run_psql(
        "SELECT COALESCE(json_agg(source_log_id)::text, '[]') FROM historical_pricing_refunds;"
    ).strip()
    try:
        return {int(item) for item in json.loads(output or "[]")}
    except ValueError as exc:
        raise RefundError("existing refund audit is invalid") from exc


def _sql(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise RefundError("non-finite SQL number")
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _validate_plan(plan, confirmation):
    if int(plan.get("version") or 0) != PLAN_VERSION or int(plan.get("issue") or 0) != ISSUE_NUMBER:
        raise RefundError("unsupported refund plan")
    calculated = _plan_digest(plan.get("refunds") or [])
    if calculated != plan.get("plan_sha256"):
        raise RefundError("refund plan checksum is invalid")
    if confirmation != calculated:
        raise RefundError("--confirm-plan-sha does not match the frozen plan")
    refunds = plan.get("refunds") or []
    if not refunds:
        raise RefundError("refund plan contains no positive refunds")
    source_ids = [int(item.get("source_log_id") or 0) for item in refunds]
    if any(source_id <= 0 for source_id in source_ids) or len(source_ids) != len(set(source_ids)):
        raise RefundError("refund plan contains invalid or duplicate source logs")
    for item in refunds:
        evidence = item.get("evidence") or {}
        if (
            int(item.get("channel_id") or 0) <= 0
            or _normal_model(item.get("source")) != _normal_model(evidence.get("source"))
            or str(item.get("beijing_date") or "") != str(evidence.get("day") or "")
            or _normal_model(item.get("model_name")) != _normal_model(evidence.get("model"))
            or evidence.get("quality") != "complete_actual_log"
        ):
            raise RefundError("refund plan contains unbound actual-cost evidence")
    totals = plan.get("totals") or {}
    expected_totals = {
        "refund_requests": len(refunds),
        "affected_users": len({int(item["user_id"]) for item in refunds}),
        "refund_quota": sum(int(item["refund_quota"]) for item in refunds),
    }
    if any(int(totals.get(key, -1)) != value for key, value in expected_totals.items()):
        raise RefundError("refund plan totals do not match request rows")
    return calculated


def create_backup(plan, target):
    user_ids = sorted({int(item["user_id"]) for item in plan["refunds"]})
    token_ids = sorted({int(item["token_id"]) for item in plan["refunds"] if int(item["token_id"]) > 0})
    log_ids = sorted({int(item["source_log_id"]) for item in plan["refunds"]})
    user_list = ",".join(map(str, user_ids)) or "0"
    token_list = ",".join(map(str, token_ids)) or "0"
    log_list = ",".join(map(str, log_ids)) or "0"
    backup = {
        "created_at": int(time.time()),
        "created_at_iso": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "plan_sha256": plan["plan_sha256"],
        "users": _json_lines(
            f"SELECT json_build_object('id',id,'username',username,'quota',quota,'used_quota',used_quota,'request_count',request_count)::text FROM users WHERE id IN ({user_list}) ORDER BY id;"
        ),
        "tokens": _json_lines(
            f"SELECT json_build_object('id',id,'user_id',user_id,'status',status,'unlimited_quota',unlimited_quota,'remain_quota',remain_quota,'used_quota',used_quota,'deleted_at',deleted_at)::text FROM tokens WHERE id IN ({token_list}) ORDER BY id;"
        ),
        "source_logs": _json_lines(
            f"SELECT json_build_object('id',id,'user_id',user_id,'token_id',token_id,'channel_id',channel_id,'created_at',created_at,'type',type,'model_name',model_name,'quota',quota,'prompt_tokens',prompt_tokens,'completion_tokens',completion_tokens,'request_id',request_id,'other_md5',md5(other))::text FROM logs WHERE id IN ({log_list}) ORDER BY id;"
        ),
    }
    if len(backup["users"]) != len(user_ids) or len(backup["source_logs"]) != len(log_ids):
        raise RefundError("backup did not capture every affected user and source log")
    write_json(target, backup)
    return backup


def _execution_sql(plan, *, commit=True):
    values = []
    for item in plan["refunds"]:
        values.append(
            "(" + ",".join(
                _sql(value) for value in (
                    item["source_log_id"], item["user_id"], item["token_id"],
                    item["channel_id"], item["source"],
                    item["model_name"], item["beijing_date"], item["original_quota"],
                    item["policy_quota"], item["refund_quota"],
                    item["source_created_at"], item["source_prompt_tokens"],
                    item["source_completion_tokens"], item["request_id"],
                    item["source_other_md5"],
                    json.dumps(item["evidence"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    item["source_fingerprint"],
                )
            ) + ")"
        )
    plan_sha = plan["plan_sha256"]
    expected = len(values)
    now = int(time.time())
    transaction_end = "COMMIT;" if commit else "ROLLBACK;"
    return f"""
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('issue-111-historical-overcharge-refunds'));
CREATE TABLE IF NOT EXISTS historical_pricing_refunds (
  source_log_id bigint PRIMARY KEY,
  issue_number integer NOT NULL,
  plan_sha256 text NOT NULL,
  user_id bigint NOT NULL,
  token_id bigint NOT NULL,
  channel_id bigint,
  upstream_source text,
  model_name text NOT NULL,
  beijing_date date NOT NULL,
  original_quota bigint NOT NULL,
  policy_quota bigint NOT NULL,
  refund_quota bigint NOT NULL CHECK (refund_quota > 0),
  evidence text NOT NULL,
  source_fingerprint text NOT NULL,
  created_at bigint NOT NULL
);
ALTER TABLE historical_pricing_refunds ADD COLUMN IF NOT EXISTS channel_id bigint;
ALTER TABLE historical_pricing_refunds ADD COLUMN IF NOT EXISTS upstream_source text;
CREATE TEMP TABLE _refund_plan (
  source_log_id bigint PRIMARY KEY, user_id bigint NOT NULL, token_id bigint NOT NULL,
  channel_id bigint NOT NULL, upstream_source text NOT NULL,
  model_name text NOT NULL, beijing_date date NOT NULL, original_quota bigint NOT NULL,
  policy_quota bigint NOT NULL, refund_quota bigint NOT NULL, source_created_at bigint NOT NULL,
  source_prompt_tokens bigint NOT NULL, source_completion_tokens bigint NOT NULL,
  request_id text NOT NULL, source_other_md5 text NOT NULL, evidence text NOT NULL,
  source_fingerprint text NOT NULL
) ON COMMIT DROP;
INSERT INTO _refund_plan VALUES {','.join(values)};
DO $verify$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM _refund_plan;
  IF n <> {expected} THEN RAISE EXCEPTION 'plan row count mismatch: %', n; END IF;
  SELECT count(*) INTO n FROM _refund_plan p JOIN logs l ON l.id=p.source_log_id
    WHERE l.type=2 AND l.user_id=p.user_id AND l.token_id=p.token_id
      AND l.channel_id=p.channel_id
      AND l.model_name=p.model_name AND l.quota=p.original_quota
      AND l.created_at=p.source_created_at AND l.prompt_tokens=p.source_prompt_tokens
      AND l.completion_tokens=p.source_completion_tokens AND l.request_id=p.request_id
      AND md5(l.other)=p.source_other_md5
      AND to_char(to_timestamp(l.created_at) AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')=p.beijing_date::text;
  IF n <> {expected} THEN RAISE EXCEPTION 'source consumption log validation failed: %/{expected}', n; END IF;
  IF EXISTS (
    SELECT 1 FROM _refund_plan p JOIN historical_pricing_refunds r USING (source_log_id)
    WHERE r.user_id<>p.user_id OR r.token_id<>p.token_id OR r.model_name<>p.model_name
      OR r.original_quota<>p.original_quota OR r.policy_quota<>p.policy_quota
      OR r.refund_quota<>p.refund_quota OR r.source_fingerprint<>p.source_fingerprint
  ) THEN RAISE EXCEPTION 'existing refund audit conflicts with frozen plan'; END IF;
  IF EXISTS (
    SELECT 1 FROM (SELECT user_id,sum(refund_quota) amount FROM _refund_plan p
      WHERE NOT EXISTS (SELECT 1 FROM historical_pricing_refunds r WHERE r.source_log_id=p.source_log_id)
      GROUP BY user_id) a LEFT JOIN users u ON u.id=a.user_id
    WHERE u.id IS NULL OR u.used_quota<a.amount
  ) THEN RAISE EXCEPTION 'user missing or used quota would underflow'; END IF;
END $verify$;
CREATE TEMP TABLE _new_refunds (source_log_id bigint PRIMARY KEY) ON COMMIT DROP;
WITH inserted AS (
  INSERT INTO historical_pricing_refunds (
    source_log_id,issue_number,plan_sha256,user_id,token_id,channel_id,upstream_source,
    model_name,beijing_date,
    original_quota,policy_quota,refund_quota,evidence,source_fingerprint,created_at
  ) SELECT source_log_id,{ISSUE_NUMBER},{_sql(plan_sha)},user_id,token_id,channel_id,
    upstream_source,model_name,
    beijing_date,original_quota,policy_quota,refund_quota,evidence,source_fingerprint,{now}
    FROM _refund_plan ON CONFLICT (source_log_id) DO NOTHING RETURNING source_log_id
) INSERT INTO _new_refunds SELECT source_log_id FROM inserted;
CREATE TEMP TABLE _updated_users (user_id bigint PRIMARY KEY) ON COMMIT DROP;
WITH amount AS (
  SELECT p.user_id,sum(p.refund_quota) refund FROM _refund_plan p JOIN _new_refunds n USING(source_log_id) GROUP BY p.user_id
), updated AS (
  UPDATE users u SET quota=u.quota+a.refund, used_quota=u.used_quota-a.refund
  FROM amount a WHERE u.id=a.user_id AND u.used_quota>=a.refund RETURNING u.id
) INSERT INTO _updated_users SELECT id FROM updated;
DO $updated_users$
DECLARE expected bigint; actual bigint;
BEGIN
  SELECT count(DISTINCT p.user_id) INTO expected FROM _refund_plan p JOIN _new_refunds n USING(source_log_id);
  SELECT count(*) INTO actual FROM _updated_users;
  IF actual<>expected THEN RAISE EXCEPTION 'atomic user refund update mismatch: %/%',actual,expected; END IF;
END $updated_users$;
CREATE TEMP TABLE _token_adjustments ON COMMIT DROP AS
  SELECT a.token_id,LEAST(t.used_quota,a.refund)::bigint adjustment
  FROM (
    SELECT p.token_id,sum(p.refund_quota)::bigint refund
    FROM _refund_plan p JOIN _new_refunds n USING(source_log_id)
    WHERE p.token_id>0 GROUP BY p.token_id
  ) a JOIN tokens t ON t.id=a.token_id;
WITH amount AS (
  SELECT p.token_id,sum(p.refund_quota) refund FROM _refund_plan p JOIN _new_refunds n USING(source_log_id)
  WHERE p.token_id>0 GROUP BY p.token_id
) UPDATE tokens t SET remain_quota=t.remain_quota+x.adjustment, used_quota=t.used_quota-x.adjustment
  FROM amount a JOIN _token_adjustments x USING(token_id) WHERE t.id=a.token_id;
WITH amount AS (
  SELECT p.user_id,sum(p.refund_quota)::bigint refund,count(*)::bigint requests
  FROM _refund_plan p JOIN _new_refunds n USING(source_log_id) GROUP BY p.user_id
) INSERT INTO logs (
  user_id,created_at,type,content,username,token_name,model_name,quota,prompt_tokens,
  completion_tokens,use_time,is_stream,channel_id,token_id,"group",ip,request_id,other
) SELECT a.user_id,{now},6,'Issue #111 historical overcharge compensation',u.username,'','',
  a.refund,0,0,0,false,0,0,'','','issue111:' || left({_sql(plan_sha)},32) || ':' || a.user_id,
  json_build_object('issue',111,'plan_sha256',{_sql(plan_sha)},'source_log_count',a.requests,
    'refund_quota',a.refund,'policy_markup',1.5)::text
  FROM amount a JOIN users u ON u.id=a.user_id;
SELECT json_build_object(
  'new_refund_requests',(SELECT count(*) FROM _new_refunds),
  'new_refund_quota',COALESCE((SELECT sum(p.refund_quota) FROM _refund_plan p JOIN _new_refunds n USING(source_log_id)),0),
  'affected_users',COALESCE((SELECT count(DISTINCT p.user_id) FROM _refund_plan p JOIN _new_refunds n USING(source_log_id)),0),
  'token_adjustment_quota',COALESCE((SELECT sum(adjustment) FROM _token_adjustments),0),
  'wallet_only_token_shortfall',COALESCE((SELECT sum(p.refund_quota) FROM _refund_plan p JOIN _new_refunds n USING(source_log_id)),0)-COALESCE((SELECT sum(adjustment) FROM _token_adjustments),0)
)::text;
{transaction_end}
"""


def apply_plan(plan, *, commit=True):
    output = _run_psql(_execution_sql(plan, commit=commit), timeout=180)
    result = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if "new_refund_requests" in candidate:
                result = candidate
    expected_end = "COMMIT" if commit else "ROLLBACK"
    if result is None or expected_end not in output:
        raise RefundError(f"refund transaction did not return {expected_end.lower()}")
    return result


def invalidate_quota_caches(plan):
    user_ids = sorted({int(item["user_id"]) for item in plan["refunds"]})
    token_ids = sorted({int(item["token_id"]) for item in plan["refunds"] if int(item["token_id"]) > 0})
    secret_command = [
        "docker", "compose", "exec", "-T", "new-api", "sh", "-c",
        'printf %s "${CRYPTO_SECRET:-${SESSION_SECRET:-}}"',
    ]
    secret_result = subprocess.run(
        secret_command, cwd=str(STACK_ROOT), capture_output=True, check=False
    )
    if secret_result.returncode != 0 or not secret_result.stdout:
        raise RefundError("database committed but quota cache secret is unavailable")
    token_keys = []
    if token_ids:
        rows = _json_lines(
            f"SELECT json_build_object('id',id,'key',\"key\")::text FROM tokens WHERE id IN ({','.join(map(str, token_ids))});"
        )
        for row in rows:
            digest = hmac.new(secret_result.stdout, row["key"].encode("utf-8"), hashlib.sha256).hexdigest()
            token_keys.append(f"token:{digest}")
    cache_keys = [f"user:{user_id}" for user_id in user_ids] + token_keys
    if not cache_keys:
        return 0
    command = ["docker", "compose", "exec", "-T", "redis", "redis-cli", "DEL", *cache_keys]
    completed = subprocess.run(
        command, cwd=str(STACK_ROOT), capture_output=True, text=True, check=False, timeout=60
    )
    if completed.returncode != 0:
        raise RefundError("database committed but affected quota caches were not invalidated")
    return len(cache_keys)


def assert_maintenance_window():
    """Refuse a financial write while public ingress can create new usage."""
    completed = subprocess.run(
        ["docker", "compose", "ps", "--status", "running", "--services"],
        cwd=str(STACK_ROOT), capture_output=True, text=True, check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise RefundError("cannot verify maintenance window")
    running = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    if "nginx" in running:
        raise RefundError("live refund requires stopped public nginx ingress")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=pathlib.Path, default=LEDGER_PATH)
    parser.add_argument("--audit", type=pathlib.Path, default=AUDIT_PATH)
    parser.add_argument("--output", type=pathlib.Path, default=PLAN_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--validate-transaction", action="store_true")
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--plan", type=pathlib.Path)
    parser.add_argument("--confirm-plan-sha", default="")
    parser.add_argument("--backup-dir", type=pathlib.Path, default=BACKUP_DIR)
    args = parser.parse_args(argv)

    if args.apply and args.validate_transaction:
        raise RefundError("choose either --apply or --validate-transaction")
    if not args.apply and not args.validate_transaction:
        ledger = read_json(args.ledger)
        audit = read_json(args.audit)
        logs = load_consumption_logs()
        existing = load_existing_refunds()
        plan = build_refund_plan(
            logs,
            build_evidence_index(ledger),
            build_channel_source_map(audit),
            existing,
        )
        write_json(args.output, plan)
        print(json.dumps({"mode": "dry_run", "output": str(args.output), "plan_sha256": plan["plan_sha256"], **plan["totals"]}, ensure_ascii=False, sort_keys=True))
        return 0

    if not args.plan:
        raise RefundError("transaction mode requires --plan")
    plan = read_json(args.plan)
    plan_sha = _validate_plan(plan, args.confirm_plan_sha)
    if args.validate_transaction:
        result = apply_plan(plan, commit=False)
        print(json.dumps({"mode": "transaction_validation", "plan_sha256": plan_sha, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    if not args.maintenance_confirmed:
        raise RefundError("--apply requires --maintenance-confirmed")
    assert_maintenance_window()
    timestamp = datetime.now(BEIJING).strftime("%Y%m%d-%H%M%S")
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(args.backup_dir, 0o700)
    backup_path = args.backup_dir / f"issue111-{timestamp}-{plan_sha[:12]}.json"
    create_backup(plan, backup_path)
    result = apply_plan(plan)
    invalidated = invalidate_quota_caches(plan) if int(result["new_refund_requests"]) > 0 else 0
    print(json.dumps({"mode": "apply", "plan_sha256": plan_sha, "backup": str(backup_path), "invalidated_cache_keys": invalidated, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RefundError as exc:
        print(f"historical refund blocked: {exc}", file=sys.stderr)
        raise SystemExit(1)
