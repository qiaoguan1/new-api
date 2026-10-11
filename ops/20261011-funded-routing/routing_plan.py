"""Pure funded-routing plans and PostgreSQL CAS SQL; never execute database I/O.

Snapshots must contain the complete, explicitly selected channel/ability row set.
The SQL checks that completeness again under locks. Normal quota, balance and
test metrics are excluded from immutable fingerprints; credentials, capabilities,
weights and settings are not. No channel or ability row is inserted or deleted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import re
from typing import NotRequired, TypedDict, cast


class ChannelRow(TypedDict):
    """Sanitized channel state; immutable_sha256 is generated in PostgreSQL."""

    id: int
    status: int
    priority: int | None
    weight: int | None
    immutable_sha256: str
    models: NotRequired[str]


class AbilityRow(TypedDict):
    """Ability state identified by the real (group, model, channel_id) key."""

    group: str
    model: str
    channel_id: int
    enabled: bool
    priority: int | None
    weight: int | None
    immutable_sha256: str


class ModelAlignment(TypedDict):
    id: int
    before_models: str
    after_models: str
    after_immutable_sha256: str


class Snapshot(TypedDict):
    """Complete sanitized target rows, in canonical deterministic order."""

    channels: list[ChannelRow]
    abilities: list[AbilityRow]


class RoutingPlan(TypedDict):
    """JSON-serializable private manifest for guarded apply and rollback."""

    schema_version: int
    first_ids: list[int]
    depleted_ids: list[int]
    target_priority: int
    before: Snapshot
    after: Snapshot
    before_hash: str
    after_hash: str
    model_alignments: list[ModelAlignment]


VOLATILE_CHANNEL_COLUMNS = (
    "status", "priority", "used_quota", "response_time", "test_time",
    "balance", "balance_updated_time",
)
_CHANNEL_KEYS = {"id", "status", "priority", "weight", "immutable_sha256"}
_ABILITY_KEYS = {"group", "model", "channel_id", "enabled", "priority", "weight", "immutable_sha256"}
_PLAN_KEYS = {
    "schema_version", "first_ids", "depleted_ids", "target_priority", "before",
    "after", "before_hash", "after_hash", "model_alignments",
}
_MAX_INT64 = 2**63 - 1


def immutable_json_sql(expression: str) -> str:
    """Fingerprint a trusted internal channel JSON expression, never user SQL."""
    columns = ",".join("'" + field + "'" for field in VOLATILE_CHANNEL_COLUMNS)
    return "encode(sha256(convert_to((" + expression + " - ARRAY[" + columns + "]::text[])::text, 'UTF8')), 'hex')"


def immutable_channel_sql(alias: str = "c", models_override: str | None = None) -> str:
    """Return the exact immutable fingerprint expression used by reader and SQL.

    Pass a simple SQL table alias, not an expression. Requires PostgreSQL's
    built-in sha256(bytea), not pgcrypto. Every unlisted channel column remains
    protected, including key, models, mapping, weight, settings and channel_info.
    """
    if not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", alias):
        raise ValueError("invalid SQL channel alias")
    expression = "to_jsonb(" + alias + ")"
    if models_override is not None:
        expression = "jsonb_set(" + expression + ", '{models}', to_jsonb(" + _sql_literal(_text(models_override, "models override")) + "::text))"
    return immutable_json_sql(expression)


def immutable_ability_sql(alias: str = "a") -> str:
    """Protect all ability fields including tag; only enabled/priority may vary."""
    if not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", alias):
        raise ValueError("invalid SQL ability alias")
    return "encode(sha256(convert_to((to_jsonb(" + alias + ") - ARRAY['enabled','priority']::text[])::text, 'UTF8')), 'hex')"


def _integer(value: object, label: str, minimum: int, maximum: int = _MAX_INT64) -> int:
    if type(value) is not int or not minimum <= cast(int, value) <= maximum:
        raise ValueError("invalid " + label)
    return cast(int, value)


def _nullable_integer(value: object, label: str, minimum: int) -> int | None:
    return None if value is None else _integer(value, label, minimum)


def _mapping(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("invalid fields in " + label)
    return cast(Mapping[str, object], value)


def _rows(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(label + " must be a complete list")
    return cast(list[object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid " + label)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("invalid Unicode in " + label) from error
    return value


def _ids(values: Iterable[int], label: str) -> list[int]:
    try:
        result = [_integer(value, label, 1, 2**31 - 1) for value in values]
    except TypeError as error:
        raise ValueError("invalid " + label) from error
    if len(result) != len(set(result)):
        raise ValueError("duplicate " + label)
    return sorted(result)


def _normalize_snapshot(value: object, targets: set[int] | None = None) -> Snapshot:
    source = _mapping(value, {"channels", "abilities"}, "snapshot")
    channels: list[ChannelRow] = []
    channel_ids: set[int] = set()
    model_presence: set[bool] = set()
    for raw in _rows(source["channels"], "channels"):
        has_models = isinstance(raw, Mapping) and "models" in raw
        model_presence.add(has_models)
        row = _mapping(raw, _CHANNEL_KEYS | ({"models"} if has_models else set()), "channel")
        channel_id = _integer(row["id"], "channel id", 1, 2**31 - 1)
        if channel_id in channel_ids:
            raise ValueError("duplicate channel id")
        channel_ids.add(channel_id)
        digest = _text(row["immutable_sha256"], "immutable digest")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid immutable digest")
        channels.append({
            "id": channel_id,
            "status": _integer(row["status"], "channel status", 1, 3),
            "priority": _nullable_integer(row["priority"], "channel priority", -2**63),
            "weight": _nullable_integer(row["weight"], "channel weight", 0),
            "immutable_sha256": digest,
        })
        if has_models:
            channels[-1]["models"] = _text(row["models"], "channel models")
    if len(model_presence) != 1:
        raise ValueError("mixed model projections are not complete")
    if not channel_ids or (targets is not None and channel_ids != targets):
        raise ValueError("channel row set does not exactly match explicit scope")
    abilities: list[AbilityRow] = []
    ability_keys: set[tuple[int, str, str]] = set()
    ability_channel_ids: set[int] = set()
    for raw in _rows(source["abilities"], "abilities"):
        row = _mapping(raw, _ABILITY_KEYS, "ability")
        channel_id = _integer(row["channel_id"], "ability channel id", 1, 2**31 - 1)
        group, model = _text(row["group"], "ability group"), _text(row["model"], "ability model")
        key = channel_id, group, model
        if channel_id not in channel_ids or key in ability_keys:
            raise ValueError("extra or duplicate ability key")
        if type(row["enabled"]) is not bool:
            raise ValueError("ability enabled must be boolean")
        digest = _text(row["immutable_sha256"], "ability immutable digest")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid ability immutable digest")
        ability_keys.add(key)
        ability_channel_ids.add(channel_id)
        abilities.append({
            "group": group, "model": model, "channel_id": channel_id,
            "enabled": cast(bool, row["enabled"]),
            "priority": _nullable_integer(row["priority"], "ability priority", -2**63),
            "weight": _nullable_integer(row["weight"], "ability weight", 0),
            "immutable_sha256": digest,
        })
    if ability_channel_ids != channel_ids:
        raise ValueError("every target channel must have its complete ability set")
    return {
        "channels": sorted(channels, key=lambda row: row["id"]),
        "abilities": sorted(abilities, key=lambda row: (row["channel_id"], row["group"], row["model"])),
    }


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid JSON manifest") from error


def snapshot_hash(snapshot: Mapping[str, object] | Snapshot) -> str:
    """Hash the sanitized controlled/immutable projection, ignoring input order.

    Extra fields are rejected rather than silently omitting protected state.
    The PostgreSQL guard produces the same compact UTF-8 JSON representation.
    """
    normalized = _normalize_snapshot(snapshot)
    return hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()


def build_plan(
    snapshot: Mapping[str, object] | Snapshot,
    first_ids: Iterable[int],
    depleted_ids: Iterable[int] = (),
    priority: int = 100,
    model_alignments: Iterable[Mapping[str, object]] = (),
) -> RoutingPlan:
    """Plan only explicitly authorized routing fields; never infer depleted IDs.

    Funded channels must already be status 1. Disabled abilities remain disabled.
    Explicit depleted channels become status 2 with every ability disabled.
    Weights and ability fingerprints remain unchanged. Only an explicitly
    approved model alignment changes a channel's immutable fingerprint. No-ops are
    valid; empty, overlapping, duplicate or incomplete scopes raise ValueError.
    """
    first, depleted = _ids(first_ids, "first id"), _ids(depleted_ids, "depleted id")
    if not (first or depleted) or set(first) & set(depleted):
        raise ValueError("routing scope must be nonempty and disjoint")
    priority = _integer(priority, "target priority", 1)
    targets = set(first) | set(depleted)
    before = _normalize_snapshot(snapshot, targets)
    after = _normalize_snapshot(before, targets)
    alignments: list[ModelAlignment] = []
    seen_alignments: set[int] = set()
    for raw in model_alignments:
        item = _mapping(raw, {"id", "before_models", "after_models", "after_immutable_sha256"}, "model alignment")
        channel_id = _integer(item["id"], "alignment id", 1, 2**31 - 1)
        if channel_id not in first or channel_id in seen_alignments:
            raise ValueError("alignment is not a unique first-tier channel")
        old = next(row for row in before["channels"] if row["id"] == channel_id)
        if "models" not in old or old["models"] != item["before_models"]:
            raise ValueError("alignment before models differs")
        old_models = _text(item["before_models"], "before models").split(",")
        new_models = _text(item["after_models"], "after models").split(",")
        if any(not name for name in old_models + new_models) or len(set(old_models)) != len(old_models) or len(set(new_models)) != len(new_models):
            raise ValueError("invalid model list")
        if not set(new_models) < set(old_models) or new_models != [name for name in old_models if name in new_models]:
            raise ValueError("alignment must only remove models without reordering")
        for removed in set(old_models) - set(new_models):
            evidence = [a for a in before["abilities"] if a["channel_id"] == channel_id and a["model"] == removed]
            if not evidence or any(a["enabled"] for a in evidence):
                raise ValueError("removed model is not completely disabled")
        digest = _text(item["after_immutable_sha256"], "aligned immutable digest")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest == old["immutable_sha256"]:
            raise ValueError("invalid aligned immutable digest")
        seen_alignments.add(channel_id)
        alignments.append(cast(ModelAlignment, dict(item)))
        new = next(row for row in after["channels"] if row["id"] == channel_id)
        new["models"] = cast(str, item["after_models"])
        new["immutable_sha256"] = digest
    for row in after["channels"]:
        if row["id"] in first:
            if row["status"] != 1:
                raise ValueError("first-tier channel is not currently enabled")
            row["priority"] = priority
        else:
            row["status"] = 2
    for ability in after["abilities"]:
        if ability["channel_id"] in first:
            if ability["enabled"]:
                ability["priority"] = priority
        else:
            ability["enabled"] = False
    return {
        "schema_version": 1, "first_ids": first, "depleted_ids": depleted,
        "target_priority": priority, "before": before, "after": after,
        "before_hash": snapshot_hash(before), "after_hash": snapshot_hash(after),
        "model_alignments": sorted(alignments, key=lambda a: a["id"]),
    }


def _validated_plan(value: Mapping[str, object] | RoutingPlan) -> RoutingPlan:
    raw = _mapping(value, _PLAN_KEYS, "plan")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("unsupported routing plan schema")
    first = cast(list[int], _rows(raw["first_ids"], "first_ids"))
    depleted = cast(list[int], _rows(raw["depleted_ids"], "depleted_ids"))
    rebuilt = build_plan(
        cast(Mapping[str, object], raw["before"]), first, depleted,
        _integer(raw["target_priority"], "target priority", 1),
        cast(list[Mapping[str, object]], _rows(raw["model_alignments"], "model alignments")),
    )
    if _canonical_json(dict(raw)) != _canonical_json(rebuilt):
        raise ValueError("routing manifest was modified or is not canonical")
    return rebuilt


def _sql_literal(value: str | int | bool | None) -> str:
    if value is None:
        return "NULL"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    return "E'" + cast(str, value).replace("\\", "\\\\").replace("'", "''") + "'"


def _projection_sql(ids: str, with_models: bool = False) -> str:
    # SELECT order is the exact alphabetical JSON key order used by sort_keys.
    # C collation matches Python Unicode ordering for valid UTF-8 text. Aggregate
    # without spaces so this is byte-identical to Python compact JSON, not jsonb
    # ::text (which inserts spaces and uses a different object key ordering).
    return "\n".join((
        "SELECT '{\"abilities\":[' || COALESCE((",
        "  SELECT string_agg(row_to_json(a)::text, ',' ORDER BY a.channel_id, a.\"group\" COLLATE \"C\", a.model COLLATE \"C\")",
        "  FROM (SELECT a.channel_id,a.enabled,a.\"group\"," + immutable_ability_sql("a") + " AS immutable_sha256,a.model,a.priority,a.weight FROM abilities AS a",
        "        WHERE channel_id IN (" + ids + ")) AS a",
        "), '') || '],\"channels\":[' || COALESCE((",
        "  SELECT string_agg(row_to_json(r)::text, ',' ORDER BY r.id)",
        "  FROM (SELECT c.id," + immutable_channel_sql("c") + " AS immutable_sha256,",
        "               " + ("c.models," if with_models else "") + "c.priority,c.status,c.weight FROM channels AS c WHERE c.id IN (" + ids + ")) AS r",
        "), '') || ']}' INTO current_projection;",
    ))


def read_projection_sql(ids: Iterable[int], with_models: bool = True) -> str:
    """Read the same canonical projection that the transaction guards hash."""
    values = _ids(ids, "projection id")
    if not values or type(with_models) is not bool:
        raise ValueError("invalid read projection scope")
    return _projection_sql(",".join(map(str, values)), with_models).replace(" INTO current_projection;", ";")


def _hash_guard(ids: str, expected_hash: str, label: str, with_models: bool) -> str:
    return (
        _projection_sql(ids, with_models) + "\n"
        "IF encode(sha256(convert_to(current_projection, 'UTF8')), 'hex') <> '" + expected_hash + "' THEN\n"
        "  RAISE EXCEPTION 'funded routing " + label + " state changed; refusing transaction';\n"
        "END IF;"
    )


def _changes(before: Snapshot, after: Snapshot) -> list[str]:
    channel_groups: dict[tuple[tuple[str, int | str | None], ...], list[int]] = {}
    for old, new in zip(before["channels"], after["channels"], strict=True):
        fields = ("status", "priority", "models") if "models" in old else ("status", "priority")
        assignments = tuple((field, cast(int | str | None, new[field])) for field in fields if old[field] != new[field])
        if assignments:
            channel_groups.setdefault(assignments, []).append(old["id"])
    ability_groups: dict[tuple[tuple[str, int | bool | None], ...], list[AbilityRow]] = {}
    for old, new in zip(before["abilities"], after["abilities"], strict=True):
        assignments = tuple((field, cast(int | bool | None, new[field])) for field in ("enabled", "priority") if old[field] != new[field])
        if assignments:
            ability_groups.setdefault(assignments, []).append(old)
    statements: list[str] = []
    for assignments, ids in channel_groups.items():
        updates = ", ".join(field + " = " + _sql_literal(value) for field, value in assignments)
        statements.append("UPDATE channels SET " + updates + " WHERE id IN (" + ",".join(map(str, ids)) + ");")
    for assignments, rows in ability_groups.items():
        updates = ", ".join(field + " = " + _sql_literal(value) for field, value in assignments)
        predicates = [
            "(channel_id = " + str(row["channel_id"]) + " AND \"group\" = " + _sql_literal(row["group"])
            + " AND model = " + _sql_literal(row["model"]) + ")" for row in rows
        ]
        statements.append("UPDATE abilities SET " + updates + " WHERE " + " OR ".join(predicates) + ";")
    return statements


def _render(plan: Mapping[str, object] | RoutingPlan, rollback: bool) -> str:
    validated = _validated_plan(plan)
    source = validated["after"] if rollback else validated["before"]
    destination = validated["before"] if rollback else validated["after"]
    source_hash = validated["after_hash"] if rollback else validated["before_hash"]
    destination_hash = validated["before_hash"] if rollback else validated["after_hash"]
    ids = ",".join(str(row["id"]) for row in source["channels"])
    with_models = "models" in source["channels"][0]
    body = "\n".join((
        "DECLARE current_projection text;",
        "BEGIN",
        "PERFORM id FROM channels WHERE id IN (" + ids + ") ORDER BY id FOR UPDATE;",
        # Row locks cannot prevent additional abilities (phantoms). The brief
        # table lock freezes only ability writes until the transaction commits.
        "LOCK TABLE abilities IN SHARE ROW EXCLUSIVE MODE;",
        "IF (SELECT count(*) FROM channels WHERE id IN (" + ids + ")) <> " + str(len(source["channels"])) + " THEN",
        "  RAISE EXCEPTION 'funded routing channel row set changed';",
        "END IF;",
        "IF (SELECT count(*) FROM abilities WHERE channel_id IN (" + ids + ")) <> " + str(len(source["abilities"])) + " THEN",
        "  RAISE EXCEPTION 'funded routing ability row set changed';",
        "END IF;",
        _hash_guard(ids, source_hash, "rollback-after" if rollback else "apply-before", with_models),
        "\n".join(_changes(source, destination)),
        _hash_guard(ids, destination_hash, "rollback-before" if rollback else "apply-after", with_models),
        "END;",
    ))
    index = 0
    delimiter = "$funded_routing_0$"
    while delimiter in body:
        index += 1
        delimiter = "$funded_routing_" + str(index) + "$"
    return "\n".join((
        "BEGIN;",
        "SET LOCAL lock_timeout = '5s';",
        "SET LOCAL statement_timeout = '15s';",
        "SELECT pg_advisory_xact_lock(hashtext('issue195-funded-routing'));",
        "DO " + delimiter,
        body,
        delimiter + ";",
        "COMMIT;",
        "",
    ))


def render_apply_sql(plan: Mapping[str, object] | RoutingPlan) -> str:
    """Render an atomic apply guarded by complete before and after state hashes.

    Only channels.status/priority, explicitly aligned channel.models, and
    abilities.enabled/priority can be updated.
    Acquires exact channel row locks and a short ability-table write lock, with
    bounded lock/statement timeouts. The caller alone owns execution authority.
    """
    return _render(plan, False)


def render_rollback_sql(plan: Mapping[str, object] | RoutingPlan) -> str:
    """Render a minimal-field reversal only when the exact after state remains.

    Any changed protected field, target row set or controlled field aborts the
    whole transaction, rather than overwriting concurrent operator changes.
    """
    return _render(plan, True)
