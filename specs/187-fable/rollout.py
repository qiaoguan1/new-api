"""Guarded, server-only incremental Fable rollout; never restarts production.

Commands: prepare, stage --db DB, activate --db DB, rollback --db DB.
prepare-validation / activate-validation require the isolated canary database,
an attested candidate manifest, and do not promote plan compatibility status.
plan.json and upstream secret files must already exist under ROOT. All stage
routes start disabled. An interrupted phase is deliberately not replayable:
inspect its private backup and reconcile the database before further action.
No command submits a generation request or modifies a production user/token.
"""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import secrets
import subprocess
import time
import urllib.parse
import urllib.request

MODEL = "claude-fable-5-1"
ROOT = Path("/opt/ai-api-stack/backups/fable187-20261007")
HELPER = Path("/opt/ai-api-stack/releases/issue173-db-authority/deploy_public.py")
CANARY_DB = "xtai_fable187_canary"
CANARY_NAME = "xtai-fable187-canary"
IMAGE_TAG = "new-api-fixed:issue183-runtime"
BASELINE_IMAGE = "sha256:8ac4d2a3590c34852de81b5e576c8212bc91304a35fbbbccd03bff117d794c97"
CANDIDATE_TAG = "new-api-fixed:issue187-fable"
SOURCE_ROOT = Path("/opt/ai-api-stack/releases/issue187-fable/source")
BASELINE_SOURCE_ROOT = Path("/opt/ai-api-stack/releases/issue183-runtime/source")
PRODUCTION_IP = "156.239.3.210"
RATIO_KEYS = ("ModelRatio", "CompletionRatio", "CacheRatio", "CreateCacheRatio")
MODE_KEY = "billing_setting.billing_mode"
EXPR_KEY = "billing_setting.billing_expr"
IDENTITY_FIELDS = ("id", "name", "type", "key", "base_url", "models", "group",
                   "priority", "weight", "model_mapping", "setting", "settings", "auto_ban")
ROUTE_COLUMNS = ",".join('"group"' if field == "group" else field for field in IDENTITY_FIELDS) + ",status"
TARGET_ROUTES = "name LIKE 'Claude187:%' OR 'claude-fable-5-1'=ANY(string_to_array(models,','))"


class RolloutError(RuntimeError):
    """A credential-free, actionable failed rollout precondition."""


def require(condition: bool, message: str) -> None:
    """Fail closed without dumping credentials, SQL, or upstream response text."""
    if not condition:
        raise RolloutError(message)


def quote(value: object) -> str:
    """Quote a PostgreSQL literal; database identifiers are fixed constants."""
    return "'" + str(value).replace("'", "''") + "'"


def private_write(path: Path, value: object) -> None:
    """Create an exclusive durable 0600 evidence file, never overwrite markers."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def validate_plan(plan: dict) -> dict:
    """Require the complete exact-model tariff and the authorized two routes."""
    require(plan.get("compatibility_verified") is True, "exact-model compatibility is unverified; rollout prohibited")
    return validate_plan_shape(plan)


def validate_plan_shape(plan: dict) -> dict:
    """Validate tariff/route shape without changing any compatibility evidence."""
    require(plan.get("model") == MODEL and plan.get("group") == "文", "wrong model or group")
    try:
        require(Decimal(str(plan.get("group_ratio"))) == Decimal("0.15"), "wrong text group ratio")
        for key in RATIO_KEYS:
            value = Decimal(str(plan.get("options", {}).get(key)))
            require(value.is_finite() and 0 < value < 10000, "incomplete or invalid ratio snapshot")
    except (InvalidOperation, ValueError, TypeError):
        raise RolloutError("invalid tariff number") from None
    require(plan.get("billing_mode") == "tiered_expr", "expression mode required")
    expression = plan.get("billing_expr")
    require(isinstance(expression, str) and bool(expression.strip()), "complete expression required")
    require("\x00" not in expression, "invalid expression")
    providers = plan.get("providers")
    require(isinstance(providers, list) and len(providers) == 2, "two verified routes required")
    require([(p.get("source"), p.get("priority")) for p in providers] ==
            [("rolldek-ccmax", 10), ("maolao", 8)], "provider or priority drift")
    return plan


def validate_candidate_manifest(candidate: dict) -> dict:
    """Pin a single image and a private, exact-baseline source attestation."""
    require(isinstance(candidate.get("image"), str) and
            re.fullmatch(r"sha256:[0-9a-f]{64}", candidate["image"]) is not None and
            candidate["image"] != BASELINE_IMAGE, "invalid candidate image digest")
    require(candidate.get("tag") == CANDIDATE_TAG and candidate.get("baseline_image") == BASELINE_IMAGE,
            "candidate tag or baseline mismatch")
    require(candidate.get("source_proof") == "runtime-source-proof.json" and
            isinstance(candidate.get("source_proof_sha256"), str) and
            re.fullmatch(r"[0-9a-f]{64}", candidate["source_proof_sha256"]) is not None,
            "candidate source proof binding missing")
    hashes = candidate.get("source_sha256")
    require(isinstance(hashes, dict) and bool(hashes), "candidate source hashes missing")
    for relative, digest in hashes.items():
        require(isinstance(relative, str) and bool(relative) and not relative.startswith("/") and
                "\\" not in relative and ":" not in relative and ".." not in PurePosixPath(relative).parts and
                isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                "invalid candidate source path or hash")
    return candidate


def validate_runtime_image(info: dict, candidate: dict | None = None) -> None:
    """Accept only the known immutable baseline or the attested exact candidate."""
    require(info.get("State", {}).get("Running") is True, "production runtime not running")
    if info.get("Image") == BASELINE_IMAGE:
        require(info.get("Config", {}).get("Image") in (IMAGE_TAG, BASELINE_IMAGE), "unknown baseline runtime tag")
        return
    require(candidate is not None and info.get("Image") == candidate["image"] and
            info.get("Config", {}).get("Image") in (CANDIDATE_TAG, candidate["image"]), "unknown production image")


def canary_environment(values: dict) -> dict:
    """Isolate both wallet/log databases and disable inherited balance polling."""
    isolated = dict(values)
    dsn = urllib.parse.urlsplit(isolated["SQL_DSN"])
    require(dsn.path == "/new-api", "unexpected production database")
    isolated["SQL_DSN"] = urllib.parse.urlunsplit(dsn._replace(path="/" + CANARY_DB))
    isolated.pop("CHANNEL_UPDATE_FREQUENCY", None)
    isolated.update(QUOTA_DB_AUTHORITATIVE="true", BATCH_UPDATE_ENABLED="false", NODE_TYPE="slave", REDIS_CONN_STRING="",
                    LOG_SQL_DSN="", MEMORY_CACHE_ENABLED="false", SQL_MAX_OPEN_CONNS="5", SQL_MAX_IDLE_CONNS="1", SQL_MAX_LIFETIME="60")
    return isolated


def transaction_guard(condition: str, message: str) -> str:
    """Use a quoted DO body so dollar signs inside option values remain data."""
    return "DO " + quote("BEGIN IF " + condition + " THEN RAISE EXCEPTION " + quote(message) + "; END IF; END;") + ";"


def advance_channel_sequence_sql(expected_sequence: str) -> str:
    """Advance only channels' verified serial allocator; never rewind it.

    The caller must already hold the channels writer-conflicting table lock.
    PostgreSQL sequence writes are nontransactional: a later rollback can leave
    a harmless ID gap, but must never restore an older allocator value.
    """
    body = """DECLARE sequence_name text; sequence_oid regclass; current_value bigint; new_max bigint;
BEGIN
  sequence_name := pg_get_serial_sequence('channels','id');
  IF sequence_name IS NULL OR sequence_name::regclass IS DISTINCT FROM %s::regclass THEN
    RAISE EXCEPTION 'channels serial allocator changed';
  END IF;
  sequence_oid := sequence_name::regclass;
  IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=sequence_oid AND relkind='S') THEN
    RAISE EXCEPTION 'channels allocator is not a sequence';
  END IF;
  EXECUTE format('SELECT last_value FROM %%s',sequence_oid) INTO current_value;
  SELECT max(id) INTO new_max FROM channels WHERE name LIKE 'Claude187:%%' AND models='claude-fable-5-1';
  IF new_max IS NULL THEN RAISE EXCEPTION 'new Fable channel IDs missing'; END IF;
  PERFORM setval(sequence_oid,GREATEST(current_value,new_max),true);
END;""" % quote(expected_sequence)
    return "DO " + quote(body) + ";"


def option_updates(plan: dict) -> dict:
    """Return only this model's six authoritative/display option entries."""
    validate_plan_shape(plan)
    return {**{key: float(plan["options"][key]) for key in RATIO_KEYS},
            MODE_KEY: "tiered_expr", EXPR_KEY: plan["billing_expr"]}


def verify_production_key(source: str, secret: dict, proof: dict, now: float | None = None) -> None:
    """Require a fresh independent attestation, not a finite probe key alone.

    Promotion is deliberately outside this script. Proofs describe the same
    secret's verified live token after an authorized production promotion.
    """
    expected_group = {"rolldek-ccmax": "ccmax", "maolao": "group_4"}.get(source)
    require(expected_group is not None and secret.get("group") == expected_group,
            "production credential group mismatch")
    require(type(secret.get("token_id")) is int and secret["token_id"] > 0 and
            type(proof.get("token_id")) is int and proof["token_id"] == secret["token_id"],
            "production credential token mismatch")
    key = secret.get("key")
    require(isinstance(key, str) and key.startswith("sk-") and "*" not in key,
            "production credential key missing")
    require(proof.get("key_sha256") == hashlib.sha256(key.encode()).hexdigest(),
            "production credential fingerprint mismatch")
    require(proof.get("group") == expected_group and proof.get("model") == MODEL and
            proof.get("allow_ips") == PRODUCTION_IP, "production credential scope mismatch")
    require(type(proof.get("status")) is int and proof["status"] == 1 and
            type(proof.get("expired_time")) is int and proof["expired_time"] == -1 and
            proof.get("unlimited_quota") is True and proof.get("cross_group_retry") is False,
            "finite or inactive production credential prohibited")
    try:
        verified_at = Decimal(str(proof.get("verified_at")))
        current = Decimal(str(time.time() if now is None else now))
        require(verified_at.is_finite() and current.is_finite() and
                Decimal(0) <= current - verified_at <= Decimal(300),
                "production credential proof stale or future dated")
    except (InvalidOperation, ValueError, TypeError):
        raise RolloutError("invalid production credential verification time") from None


def require_ratio_maps(current: dict) -> None:
    """Never replace missing native default snapshots with a target-only map."""
    for key in RATIO_KEYS:
        require(key in current, "ratio map missing; explicit native snapshot required")
        require(isinstance(json.loads(current[key]), dict), "ratio option is not a model map")


def merge_options(current: dict, updates: dict) -> dict:
    """Preserve every unrelated entry when adding the one exact model."""
    result = {}
    for key, entry in updates.items():
        previous = json.loads(current[key]) if key in current else {}
        require(isinstance(previous, dict), "option is not a model map")
        result[key] = json.dumps({**previous, MODEL: entry}, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return result


def restore_options(current: dict, before: dict, expected: dict) -> dict:
    """Restore only our target entry; reject a concurrent target-price change."""
    restored = {}
    for key, expected_entry in expected.items():
        require(key in current, "staged price option disappeared")
        live = json.loads(current[key])
        require(isinstance(live, dict) and live.get(MODEL) == expected_entry, "target price changed; rollback blocked")
        old = json.loads(before[key]) if key in before else {}
        require(isinstance(old, dict), "invalid option backup")
        if MODEL in old:
            live[MODEL] = old[MODEL]
        else:
            live.pop(MODEL, None)
        restored[key] = json.dumps(live, ensure_ascii=False, sort_keys=True, allow_nan=False) if live or key in before else None
    return restored


def option_guard(key: str, raw: str | None) -> str:
    """Guard both existing values and missing rows inside the locked transaction."""
    if raw is None:
        condition = "EXISTS(SELECT 1 FROM options WHERE key=" + quote(key) + ")"
    else:
        condition = "NOT EXISTS(SELECT 1 FROM options WHERE key=" + quote(key) + " AND value=" + quote(raw) + ")"
    return transaction_guard(condition, "concurrent option change")


def validate_routes(live: list, abilities: list, saved: list, status: int | None) -> None:
    """Check IDs, credentials, native format, model, group and all route settings."""
    expected = {row["id"]: row for row in saved}
    require(len(live) == len(saved) and {row["id"] for row in live} == set(expected), "route set changed")
    for row in live:
        original = expected[row["id"]]
        require(all(row.get(field) == original.get(field) for field in IDENTITY_FIELDS), "route identity changed")
        require(row["status"] == status if status is not None else row["status"] in (1, 2), "route status changed")
    require(len(abilities) == len(saved) and {row["channel_id"] for row in abilities} == set(expected), "ability set changed")
    for row in abilities:
        original = expected[row["channel_id"]]
        require(row["model"] == MODEL and row["group"] == "文" and row["priority"] == original["priority"]
                and row["weight"] == original["weight"], "ability identity changed")
        if status is not None:
            require(row["enabled"] is (status == 1), "ability status changed")


class Rollout:
    """Scoped PostgreSQL operations through the established server-side helper."""

    def __init__(self, db: str = "new-api", root: Path = ROOT, helper=None,
                 source_root: Path = SOURCE_ROOT, baseline_source_root: Path = BASELINE_SOURCE_ROOT):
        require(db in ("new-api", CANARY_DB), "database outside authorized scope")
        self.db, self.root, self._helper = db, Path(root), helper
        self.source_root, self.baseline_source_root = Path(source_root), Path(baseline_source_root)

    @property
    def h(self):
        """Lazily import the helper; offline imports/tests have no server effects."""
        if self._helper is None:
            spec = importlib.util.spec_from_file_location("fable187_helpers", HELPER)
            require(spec is not None and spec.loader is not None, "deployment helper missing")
            self._helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._helper)
        self._helper.BACKUP = self.root
        return self._helper

    def read(self, name: str):
        """Read private JSON without exposing its contents to stdout."""
        path = self.root / name
        require(not path.is_symlink() and (os.name == "nt" or path.stat().st_mode & 0o077 == 0), "private evidence permissions unsafe")
        return json.loads(path.read_text(encoding="utf-8"))

    def phase(self, name: str) -> Path:
        """Resolve a database-qualified phase filename in the fixed private root."""
        return self.root / (self.db + "-" + name + ".json")

    def load_plan(self) -> dict:
        """Reject unverified candidates before loading helpers or touching systems."""
        return validate_plan(self.read("plan.json"))

    def validation_scope(self, validation: bool) -> None:
        """Protect public and internal phase entry points from production bypass."""
        require(type(validation) is bool and (not validation or self.db == CANARY_DB), "validation is canary-only")

    def load_validation_plan(self) -> dict:
        """Read an unverified candidate only for an explicit isolated phase."""
        self.validation_scope(True)
        plan = self.read("plan.json")
        require(type(plan.get("compatibility_verified")) is bool, "explicit candidate compatibility flag required")
        return validate_plan_shape(plan)

    def load_candidate(self) -> dict:
        """Verify private proof bytes and both current/baseline source trees."""
        candidate = validate_candidate_manifest(self.read("runtime-candidate.json"))
        proof = self.read(candidate["source_proof"])
        require(hashlib.sha256((self.root / candidate["source_proof"]).read_bytes()).hexdigest() == candidate["source_proof_sha256"],
                "candidate source proof changed")
        require(proof.get("image") == candidate["image"] and proof.get("baseline_image") == BASELINE_IMAGE and
                proof.get("source_sha256") == candidate["source_sha256"], "candidate source proof identity mismatch")
        baseline_hashes = proof.get("baseline_source_sha256")
        new_files = proof.get("new_files")
        require(isinstance(baseline_hashes, dict) and set(baseline_hashes) == set(candidate["source_sha256"]) and
                isinstance(new_files, list) and all(isinstance(path, str) for path in new_files) and
                len(new_files) == len(set(new_files)) and set(new_files) == {path for path, digest in baseline_hashes.items() if digest is None},
                "baseline source proof coverage mismatch")
        require(type(proof.get("unchanged_source_count")) is int and proof["unchanged_source_count"] > 0,
                "unchanged source verification missing")
        for relative, digest in candidate["source_sha256"].items():
            path = self.source_root / relative
            baseline = self.baseline_source_root / relative
            require(not path.is_symlink() and path.resolve().is_relative_to(self.source_root.resolve()) and path.is_file(),
                    "candidate source path unsafe or missing")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "candidate source changed")
            require(not baseline.is_symlink() and baseline.resolve().is_relative_to(self.baseline_source_root.resolve()),
                    "baseline source path unsafe")
            baseline_digest = baseline_hashes[relative]
            if baseline_digest is None:
                require(relative in new_files and not baseline.exists(), "new source exists in baseline")
            else:
                require(isinstance(baseline_digest, str) and re.fullmatch(r"[0-9a-f]{64}", baseline_digest) is not None and
                        baseline.is_file() and hashlib.sha256(baseline.read_bytes()).hexdigest() == baseline_digest,
                        "baseline source changed or missing")
        return candidate

    def credentials(self, plan: dict) -> dict:
        """Read scoped secrets; production always requires fresh long-lived proof."""
        credentials = {}
        for provider in plan["providers"]:
            source = provider["source"]
            secret = self.read(source + "-secret.json")
            expected_group = "ccmax" if source == "rolldek-ccmax" else "group_4"
            expected_url = "https://rolldek.com" if source == "rolldek-ccmax" else "https://api.maolaoapi.com"
            require(secret.get("group") == expected_group and secret.get("api_origin") == expected_url,
                    "upstream secret scope changed")
            require(isinstance(secret.get("key"), str) and secret["key"].startswith("sk-") and "*" not in secret["key"], "invalid private upstream key")
            if self.db == "new-api":
                proof_name = source + "-production-key-verified.json"
                require((self.root / proof_name).is_file(), "production key proof missing; probe key cannot be staged")
                verify_production_key(source, secret, self.read(proof_name))
            credentials[source] = secret
        return credentials

    def sql(self, command: str) -> str:
        """Never propagate database stderr that might contain inserted key values."""
        try:
            return self.h.sql(command, self.db)
        except Exception:
            raise RolloutError("database operation failed; inspect private phase evidence") from None

    def rows(self, query: str) -> list:
        """Read structured SQL rows without printing them."""
        return json.loads(self.sql("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM (" + query + ") t;"))

    def options(self) -> dict:
        """Read only the six target option maps and the immutable group ratio gate."""
        keys = list(RATIO_KEYS) + [MODE_KEY, EXPR_KEY, "GroupRatio"]
        rows = self.rows("SELECT key,value FROM options WHERE key IN (" + ",".join(map(quote, keys)) + ")")
        result = {row["key"]: row["value"] for row in rows}
        require("GroupRatio" in result and Decimal(str(json.loads(result["GroupRatio"])["文"])) == Decimal("0.15"), "text group ratio changed")
        return result

    def image(self) -> dict:
        """Pin the immutable ID of the current, expected production image."""
        info = self.h.inspect(self.h.NATIVE)
        candidate = None if info.get("Image") == BASELINE_IMAGE else self.load_candidate()
        validate_runtime_image(info, candidate)
        return info

    def snapshot_routes(self) -> tuple[list, list, list]:
        """Read the task prefix and exact model, including unexpected extra routes."""
        routes = self.rows("SELECT " + ROUTE_COLUMNS + " FROM channels WHERE " + TARGET_ROUTES + " ORDER BY id")
        abilities = self.rows("SELECT channel_id,model,\"group\",enabled,priority,weight FROM abilities WHERE model=" + quote(MODEL) + " OR channel_id IN (SELECT id FROM channels WHERE name LIKE 'Claude187:%') ORDER BY channel_id")
        metadata = self.rows("SELECT id,model_name,status FROM models WHERE model_name=" + quote(MODEL) + " AND deleted_at IS NULL")
        return routes, abilities, metadata

    def channel_sequence_snapshot(self) -> dict:
        """Record the existing channels-only serial identity and counter privately."""
        names = self.rows("SELECT pg_get_serial_sequence('channels','id') AS sequence")
        require(len(names) == 1 and isinstance(names[0].get("sequence"), str) and
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", names[0]["sequence"]) is not None,
                "channels serial sequence missing or unsupported identifier")
        name = names[0]["sequence"]
        # pg_get_serial_sequence supplied the qualified name, additionally
        # restricted to a simple identifier before using it in the read query.
        state = self.rows("SELECT last_value,is_called FROM " + name)
        require(len(state) == 1 and type(state[0].get("last_value")) is int and
                type(state[0].get("is_called")) is bool, "channels sequence state unavailable")
        return {"sequence": name, "last_value": state[0]["last_value"], "is_called": state[0]["is_called"]}

    def begin_phase(self, phase: str, evidence: dict) -> None:
        """Reserve an exclusive marker before any mutating or ambiguous operation."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        require(not self.phase("rollback").exists(), "rollout already rolled back")
        private_write(self.phase(phase + "-started"), {"at": int(time.time()), **evidence})

    def stage(self) -> None:
        """Back up and transactionally insert two disabled native Claude routes."""
        self._stage()

    def _stage(self, validation: bool = False) -> None:
        """Shared staging engine; the unverified route is explicitly canary-only."""
        self.validation_scope(validation)
        plan = self.load_validation_plan() if validation else self.load_plan()
        candidate = self.load_candidate() if validation else None
        require(not self.phase("stage").exists(), "stage already completed")
        # Credential prerequisites precede even helper loading/read-only SQL.
        credentials = self.credentials(plan)
        info = self.image()
        target_image = candidate["image"] if validation else info["Image"]
        native_values = self.h.env(info)
        expected_dsn = native_values["SQL_DSN"] if self.db == "new-api" else canary_environment(native_values)["SQL_DSN"]
        require(urllib.parse.urlsplit(expected_dsn).path == "/" + self.db, "unexpected production database")
        dsn_fingerprint = hashlib.sha256(expected_dsn.encode()).hexdigest()
        current = self.options()
        require_ratio_maps(current)
        routes, abilities, metadata = self.snapshot_routes()
        require(not routes and not abilities and not metadata, "target or task routes already exist")
        channel_sequence_before = self.channel_sequence_snapshot()
        updates = option_updates(plan)
        merged = merge_options(current, updates)
        fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.begin_phase("stage", {"plan_sha256": fingerprint})
        private_write(self.phase("before"), {"options": current, "channels": routes, "abilities": abilities, "models": metadata,
                                            "image": target_image, "baseline_image": BASELINE_IMAGE, "production_image": info["Image"],
                                            "validation_only": validation, "channels_sequence_before": channel_sequence_before,
                                            "dsn_sha256": dsn_fingerprint, "updates": updates, "plan_sha256": fingerprint})
        statements = ["BEGIN;", "SET LOCAL lock_timeout='5s';",
                      "LOCK TABLE options,channels,abilities,models IN SHARE ROW EXCLUSIVE MODE;",
                      option_guard("GroupRatio", current["GroupRatio"]),
                      transaction_guard("EXISTS(SELECT 1 FROM channels WHERE " + TARGET_ROUTES + ") OR EXISTS(SELECT 1 FROM abilities WHERE model=" + quote(MODEL) + ") OR EXISTS(SELECT 1 FROM models WHERE model_name=" + quote(MODEL) + " AND deleted_at IS NULL)", "concurrent target route insertion")]
        for key, value in merged.items():
            statements += [option_guard(key, current.get(key)),
                           "INSERT INTO options(key,value) VALUES(" + quote(key) + "," + quote(value) + ") ON CONFLICT(key) DO UPDATE SET value=excluded.value;"]
        endpoints = {"openai": {"path": "/v1/chat/completions", "method": "POST"},
                     "anthropic": {"path": "/v1/messages", "method": "POST"}}
        statements.append("INSERT INTO models(model_name,description,icon,tags,vendor_id,endpoints,status,sync_official,created_time,updated_time,name_rule) VALUES(" +
                          ",".join([quote(MODEL), quote("Canary-only Fable compatibility candidate; issue187" if validation else "Verified upstream Fable label; issue187"), quote("Claude"), quote("text"), "38", quote(json.dumps(endpoints)), "0", "0", str(int(time.time())), str(int(time.time())), "0"]) + ");")
        for provider in plan["providers"]:
            source = provider["source"]
            secret = credentials[source]
            setting = {"force_format": False, "thinking_to_content": False, "proxy": "", "pass_through_body_enabled": False,
                       "system_prompt": "", "system_prompt_override": False}
            settings = {"upstream_model_update_check_enabled": False, "upstream_model_update_auto_sync_enabled": False}
            # Historical explicit-ID rollouts can leave channels_id_seq behind.
            # The enclosing SHARE ROW EXCLUSIVE table lock serializes this
            # allocation with normal writers. After both inserts, the channels
            # serial allocator is monotonically aligned to the new IDs below.
            values = ["(SELECT coalesce(max(id),0)+1 FROM channels)", "14", quote(secret["key"]), "2", quote("Claude187:" + source + ":" + MODEL), "0", str(int(time.time())),
                      quote(secret["api_origin"]), quote(MODEL), quote("文"), str(provider["priority"]), "1", quote(""),
                      quote(json.dumps(setting)), quote(json.dumps({"is_multi_key": False})), quote(json.dumps(settings)), quote("issue187 verified isolated exact-model native Claude route")]
            statements.append("WITH added AS (INSERT INTO channels(id,type,key,status,name,weight,created_time,base_url,models,\"group\",priority,auto_ban,model_mapping,setting,channel_info,settings,remark) VALUES(" +
                              ",".join(values) + ") RETURNING id) INSERT INTO abilities(\"group\",model,channel_id,enabled,priority,weight) SELECT '文'," + quote(MODEL) + ",id,false," + str(provider["priority"]) + ",0 FROM added;")
        # Snapshot identities inside the transaction, before releasing table locks.
        statements.append(advance_channel_sequence_sql(channel_sequence_before["sequence"]))
        statements.append("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM (SELECT " + ROUTE_COLUMNS + " FROM channels WHERE " + TARGET_ROUTES + " ORDER BY id) t;")
        result = self.sql("\n".join(statements + ["COMMIT;"]))
        snapshots = [json.loads(line) for line in result.splitlines() if line.startswith("[")]
        require(len(snapshots) == 1 and len(snapshots[0]) == 2, "transaction route snapshot missing; reconcile stage")
        committed_routes = snapshots[0]
        routes, abilities, metadata = self.snapshot_routes()
        require(len(routes) == 2 and len(metadata) == 1 and metadata[0]["status"] == 0, "stage result incomplete")
        validate_routes(routes, abilities, committed_routes, 2)
        private_write(self.phase("stage"), {"at": int(time.time()), "channels": routes, "metadata": metadata,
                                           "updates": updates, "image": target_image, "baseline_image": BASELINE_IMAGE, "production_image": info["Image"],
                                           "validation_only": validation, "dsn_sha256": dsn_fingerprint, "plan_sha256": fingerprint})
        print(json.dumps({"phase": "stage", "database": self.db, "model": MODEL, "disabled_routes": len(routes)}))

    def runtime_gate(self, saved: dict) -> dict:
        """Require the loaded six prices and .15 group ratio on the pinned runtime."""
        self.validation_scope(saved.get("validation_only", False))
        require(self.image()["Image"] == saved.get("production_image", saved["image"]), "production immutable image changed")
        if saved.get("validation_only"):
            require(self.load_candidate()["image"] == saved["image"], "validation candidate changed")
        native = self.h.NATIVE if self.db == "new-api" else CANARY_NAME
        runtime = self.h.inspect(native)
        require(runtime["State"]["Running"] and runtime["Image"] == saved["image"], "wrong verification runtime")
        values = self.h.env(runtime)
        require(urllib.parse.urlsplit(values["SQL_DSN"]).path == "/" + self.db, "runtime attached to wrong database")
        require(hashlib.sha256(values["SQL_DSN"].encode()).hexdigest() == saved["dsn_sha256"], "runtime database authority changed")
        if self.db == CANARY_DB:
            require(not values.get("LOG_SQL_DSN") and not values.get("REDIS_CONN_STRING") and
                    "CHANNEL_UPDATE_FREQUENCY" not in values and all(values.get(key) == expected for key, expected in {
                        "QUOTA_DB_AUTHORITATIVE": "true", "BATCH_UPDATE_ENABLED": "false", "NODE_TYPE": "slave",
                        "MEMORY_CACHE_ENABLED": "false", "SQL_MAX_OPEN_CONNS": "5", "SQL_MAX_IDLE_CONNS": "1", "SQL_MAX_LIFETIME": "60"}.items()),
                    "canary external state isolation changed")
        admins = self.rows("SELECT id,trim(access_token) AS token FROM users WHERE id=1 AND role=100 AND status=1")
        require(len(admins) == 1 and bool(admins[0]["token"]), "existing admin access unavailable")
        request = urllib.request.Request(self.h.address(native, 3000) + "/api/option/",
                                         headers={"Authorization": "Bearer " + admins[0]["token"], "New-Api-User": str(admins[0]["id"])})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.load(response)
        except Exception:
            raise RolloutError("runtime option read failed") from None
        require(body.get("success") is True and isinstance(body.get("data"), list), "runtime options unavailable")
        options = {row["key"]: row["value"] for row in body["data"]}
        require(Decimal(str(json.loads(options.get("GroupRatio", "{}"))["文"])) == Decimal("0.15"), "runtime text ratio changed")
        for key, value in saved["updates"].items():
            require(key in options and json.loads(options[key]).get(MODEL) == value, "runtime model pricing incomplete or stale")
        return {"at": int(time.time()), "image": saved["image"], "model": MODEL, "group_ratio": "0.15", "verified_option_keys": list(saved["updates"])}

    def route_guards(self, saved: dict, status: int | None) -> list:
        """Recheck each immutable route and ability while tables are locked."""
        guards = []
        ids = ",".join(str(row["id"]) for row in saved["channels"])
        guards.append(transaction_guard("(SELECT count(*) FROM channels WHERE " + TARGET_ROUTES + ")<>2 OR (SELECT count(*) FROM abilities WHERE model=" + quote(MODEL) + " OR channel_id IN (" + ids + "))<>2", "target route set changed"))
        for row in saved["channels"]:
            tests = [('"group"' if field == "group" else field) + " IS NOT DISTINCT FROM " + ("NULL" if row.get(field) is None else quote(row[field])) for field in IDENTITY_FIELDS]
            tests.append("status=" + str(status) if status is not None else "status IN (1,2)")
            ability = "channel_id=" + str(row["id"]) + " AND model=" + quote(MODEL) + " AND \"group\"='文' AND priority=" + str(row["priority"]) + " AND weight=" + str(row["weight"])
            if status is not None:
                ability += " AND enabled=" + ("true" if status == 1 else "false")
            guards.append(transaction_guard("NOT EXISTS(SELECT 1 FROM channels WHERE " + " AND ".join(tests) + ") OR NOT EXISTS(SELECT 1 FROM abilities WHERE " + ability + ")", "route identity changed"))
        model = saved["metadata"][0]
        model_condition = "id=" + str(model["id"]) + " AND model_name=" + quote(MODEL) + " AND deleted_at IS NULL AND " + ("status=0" if status == 2 else "status IN (0,1)")
        guards.append(transaction_guard("(SELECT count(*) FROM models WHERE model_name=" + quote(MODEL) + " AND deleted_at IS NULL)<>1 OR NOT EXISTS(SELECT 1 FROM models WHERE " + model_condition + ")", "model metadata changed"))
        return guards

    def activate(self) -> None:
        """Activate only after exact route, database, runtime and tariff checks."""
        self._activate()

    def activate_validation(self) -> None:
        """Enable temporary canary routes without granting production compatibility."""
        self.validation_scope(True)
        self._activate(validation=True)

    def _activate(self, validation: bool = False) -> None:
        """Shared activation engine; production evidence requirements never relax."""
        self.validation_scope(validation)
        plan = self.load_validation_plan() if validation else self.load_plan()
        phase = "activate-validation" if validation else "activate"
        require(not self.phase(phase).exists(), "already activated")
        saved = self.read(self.db + "-stage.json")
        require(saved.get("validation_only", False) is validation, "staged phase scope mismatch")
        fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        require(saved["plan_sha256"] == fingerprint, "plan changed after stage")
        if self.db == "new-api":
            credentials = self.credentials(plan)
            staged_keys = {row["name"]: row["key"] for row in saved["channels"]}
            require(all(staged_keys.get("Claude187:" + source + ":" + MODEL) == secret["key"]
                        for source, secret in credentials.items()), "fresh production credential does not match staged route")
            require(time.time() - saved["at"] >= 65, "wait for one complete pricing synchronization interval")
            # Raw /api/option strings alone cannot prove expression settlement.
            proof = self.read("canary-verified.json")
            require(proof.get("model") == MODEL and proof.get("plan_sha256") == fingerprint and
                    proof.get("image") == saved["image"] and proof.get("compatibility_verified") is True and
                    proof.get("billing_exact") is True and proof.get("runtime_pricing_verified") is True,
                    "independent exact-plan canary verification required")
        routes, abilities, metadata = self.snapshot_routes()
        validate_routes(routes, abilities, saved["channels"], 2)
        require(metadata == saved["metadata"], "staged model metadata changed")
        current = self.options()
        for key, value in saved["updates"].items():
            require(key in current and json.loads(current[key]).get(MODEL) == value, "database pricing changed")
        runtime = self.runtime_gate(saved)
        self.begin_phase(phase, runtime)
        ids = ",".join(str(row["id"]) for row in saved["channels"])
        statements = ["BEGIN;", "SET LOCAL lock_timeout='5s';", "LOCK TABLE options,channels,abilities,models IN SHARE ROW EXCLUSIVE MODE;"]
        statements += [option_guard(key, current.get(key)) for key in list(saved["updates"]) + ["GroupRatio"]]
        statements += self.route_guards(saved, 2)
        statements += ["UPDATE channels SET status=1 WHERE id IN (" + ids + ");",
                       "UPDATE abilities SET enabled=true WHERE channel_id IN (" + ids + ") AND model=" + quote(MODEL) + " AND \"group\"='文';",
                       "UPDATE models SET status=1 WHERE id=" + str(saved["metadata"][0]["id"]) + ";", "COMMIT;"]
        self.sql("\n".join(statements))
        routes, abilities, metadata = self.snapshot_routes()
        validate_routes(routes, abilities, saved["channels"], 1)
        require(len(metadata) == 1 and metadata[0]["id"] == saved["metadata"][0]["id"] and metadata[0]["status"] == 1, "activation incomplete")
        private_write(self.phase(phase), {**runtime, "validation_only": validation})
        print(json.dumps({"phase": phase, "database": self.db, "model": MODEL, "routes": len(routes)}))

    def rollback(self) -> None:
        """Disable our rows and restore only this model's prior six price entries."""
        saved = self.read(self.db + "-stage.json")
        self.validation_scope(saved.get("validation_only", False))
        before = self.read(self.db + "-before.json")
        routes, abilities, metadata = self.snapshot_routes()
        validate_routes(routes, abilities, saved["channels"], None)
        current = self.options()
        restored = restore_options(current, before["options"], saved["updates"])
        self.begin_phase("rollback", {"image": saved["image"], "channels": routes, "abilities": abilities, "metadata": metadata, "options": current})
        ids = ",".join(str(row["id"]) for row in saved["channels"])
        statements = ["BEGIN;", "SET LOCAL lock_timeout='5s';", "LOCK TABLE options,channels,abilities,models IN SHARE ROW EXCLUSIVE MODE;"]
        statements += self.route_guards(saved, None)
        statements += [option_guard(key, current.get(key)) for key in list(restored) + ["GroupRatio"]]
        statements += ["UPDATE channels SET status=2 WHERE id IN (" + ids + ");",
                       "UPDATE abilities SET enabled=false WHERE channel_id IN (" + ids + ") AND model=" + quote(MODEL) + " AND \"group\"='文';",
                       "UPDATE models SET status=0 WHERE id=" + str(saved["metadata"][0]["id"]) + ";"]
        for key, raw in restored.items():
            statements.append("DELETE FROM options WHERE key=" + quote(key) + ";" if raw is None else "UPDATE options SET value=" + quote(raw) + " WHERE key=" + quote(key) + ";")
        self.sql("\n".join(statements + ["COMMIT;"]))
        routes, abilities, metadata = self.snapshot_routes()
        validate_routes(routes, abilities, saved["channels"], 2)
        private_write(self.phase("rollback"), {"at": int(time.time()), "model": MODEL, "disabled_ids": [row["id"] for row in routes]})
        print(json.dumps({"phase": "rollback", "database": self.db, "model": MODEL, "disabled_routes": len(routes)}))

    def prepare(self) -> None:
        """Clone production privately and start only a small isolated canary."""
        self._prepare()

    def prepare_validation(self) -> None:
        """Start an attested candidate only in the isolated validation database."""
        self.validation_scope(True)
        self._prepare(validation=True)

    def _prepare(self, validation: bool = False) -> None:
        """Shared clone/start engine; no production container is ever modified."""
        self.validation_scope(validation)
        require(self.db == CANARY_DB, "prepare only operates on isolated database")
        self.load_validation_plan() if validation else self.load_plan()
        candidate = self.load_candidate() if validation else None
        info = self.image()
        target_image = candidate["image"] if validation else info["Image"]
        phase = "prepare-validation" if validation else "prepare"
        require(not self.h.sql("SELECT 1 FROM pg_database WHERE datname=" + quote(CANARY_DB) + ";"), "canary database already exists")
        inspected = subprocess.run(["docker", "inspect", CANARY_NAME], capture_output=True, text=True)
        require(inspected.returncode != 0, "canary container already exists")
        self.begin_phase(phase, {"image": target_image, "baseline_image": BASELINE_IMAGE, "production_image": info["Image"], "validation_only": validation})
        private_write(self.phase("native-before"), info)
        dump = self.root / "fable187-production-before.dump"
        with os.fdopen(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            subprocess.run(["docker", "exec", self.h.PG, "pg_dump", "-U", "newapi", "-d", "new-api", "-Fc"], stdout=handle, stderr=subprocess.PIPE, check=True)
        self.h.sql("CREATE DATABASE " + CANARY_DB + " OWNER newapi;")
        with dump.open("rb") as handle:
            subprocess.run(["docker", "exec", "-i", self.h.PG, "pg_restore", "-U", "newapi", "-d", CANARY_DB, "--no-owner"], stdin=handle, capture_output=True, check=True)
        self._stage(validation=validation)
        values = canary_environment(self.h.env(info))
        self.h.run(CANARY_NAME, target_image, values, extra=["--memory", "768m"])
        # This account exists only in the clone. Production wallets are untouched.
        require(not self.rows("SELECT id FROM users WHERE username='fable187-canary'"), "canary account already exists")
        uid = int(self.sql("INSERT INTO users(username,password,role,status,quota,used_quota,request_count,\"group\",aff_code,created_at) VALUES('fable187-canary','!nonlogin-test-account',1,1,500000,0,0,'default'," + quote(secrets.token_hex(5)) + "," + str(int(time.time())) + ") RETURNING id;").splitlines()[0])
        key = secrets.token_hex(24)
        tid = int(self.sql("INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,\"group\",cross_group_retry) VALUES(" + str(uid) + "," + quote(key) + ",1,'fable187-canary'," + str(int(time.time())) + ",0," + str(int(time.time()) + 3600) + ",500000,false,true," + quote(MODEL) + ",'',0,'auto',false) RETURNING id;").splitlines()[0])
        private_write(self.root / "canary-account.json", {"user_id": uid, "token_id": tid, "key": "sk-" + key, "initial_quota": 500000})
        private_write(self.phase(phase), {"at": int(time.time()), "image": target_image, "baseline_image": BASELINE_IMAGE,
                                        "production_image": info["Image"], "validation_only": validation, "database": CANARY_DB, "dump_bytes": dump.stat().st_size})
        print(json.dumps({"phase": phase, "database": self.db, "model": MODEL, "canary": CANARY_NAME, "routes_disabled": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "stage", "activate", "rollback", "prepare-validation", "activate-validation"])
    parser.add_argument("--db", choices=["new-api", CANARY_DB], default="new-api")
    args = parser.parse_args()
    try:
        getattr(Rollout(db=args.db), args.phase.replace("-", "_"))()
    except Exception as error:
        # Deliberately do not print arbitrary exception text or a traceback.
        print(json.dumps({"phase": args.phase, "database": args.db, "completed": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, RolloutError) else "external operation failed",
                          "next_step": "inspect private phase evidence; do not replay ambiguous writes"}))
        raise SystemExit(1) from None
