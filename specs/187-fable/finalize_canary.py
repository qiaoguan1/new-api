"""Promote verified Fable evidence after read-only isolated runtime checks.

Run this on the prepared host only after the image-bound validation pipeline.
It makes no generation request or production database/runtime operation. Only
plan.json's compatibility flag and exclusive private evidence files are written.
An interrupted finalization is not automatically replayed; reconcile its backup.
"""

from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.parse
import urllib.request


spec = importlib.util.spec_from_file_location("fable187_rollout", Path(__file__).with_name("rollout.py"))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

DIMENSIONS = {"p": "input", "c": "output", "cr": "cache_read", "cc": "cache_write_5m", "cc1h": "cache_write_1h"}
STANDARD = {"input": "20.085", "output": "100.425", "cache_read": "0.502125",
            "cache_write_5m": "25.10625", "cache_write_1h": "40.17"}
FAKE_CASES = frozenset((path, case) for path in ("/v1/messages", "/v1/chat/completions")
                       for case in ("total-only", "5m", "1h", "mixed", "mixed-max", "mixed-max-stream"))
INVALID_PATHS = ("/v1/chat/completions", "/v1/messages", "/v1/messages", "/v1/chat/completions", "/v1/responses")
REAL_CASES = frozenset((source, path, effort) for source, paths in (
    ("rolldek-ccmax", ("/v1/messages", "/v1/chat/completions")), ("maolao", ("/v1/messages",)))
    for path in paths for effort in ("", "max"))
PROOF_NAMES = ("validation-pipeline-result.json", "fake-verified.json", "real-verified.json")


def plan_sha256(plan: dict) -> str:
    """Use precisely the same canonical plan digest as rollout staging."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_tariff(plan: dict) -> None:
    """Pin all five reviewed dimensions and both effort tariffs, not just totals."""
    r.validate_plan_shape(plan)
    retail = plan.get("retail_cny_per_m")
    r.require(isinstance(retail, dict) and set(retail) == {"standard", "max"}, "exact retail vectors missing")
    try:
        for tier, multiplier in (("standard", 1), ("max", 3)):
            values = retail.get(tier)
            r.require(isinstance(values, dict) and set(values) == set(STANDARD), "retail dimensions incomplete")
            for key, value in STANDARD.items():
                r.require(Decimal(str(values[key])) == Decimal(value) * multiplier, "reviewed retail vector changed")
        expected_options = {"ModelRatio": "66.95", "CompletionRatio": "5", "CacheRatio": "0.025", "CreateCacheRatio": "1.25"}
        for key, value in expected_options.items():
            r.require(Decimal(str(plan["options"][key])) == Decimal(value), "reviewed display ratio changed")
        terms = {}
        for tier in ("standard", "max"):
            terms[tier] = " + ".join(variable + " * " + format((Decimal(str(retail[tier][dimension])) / Decimal("0.15")).normalize(), "f")
                                     for variable, dimension in DIMENSIONS.items())
        expression = ('(param("output_config.effort") == "max" || param("reasoning_effort") == "max") ? tier("max", ' +
                      terms["max"] + ') : tier("standard", ' + terms["standard"] + ')')
        r.require(plan["billing_expr"] == expression, "reviewed billing expression changed")
    except (InvalidOperation, ValueError, TypeError, KeyError):
        raise r.RolloutError("invalid exact retail tariff") from None


def expected_quota(plan: dict, params: dict, maximum: bool) -> int:
    """Independently recompute the reviewed quota convention without floats."""
    r.require(isinstance(params, dict) and set(params) == set(DIMENSIONS) and type(maximum) is bool,
              "token vector shape changed")
    r.require(all(type(value) is int and value >= 0 for value in params.values()), "invalid token vector")
    prices = plan["retail_cny_per_m"]["max" if maximum else "standard"]
    value = sum(Decimal(params[key]) * Decimal(str(prices[dimension])) for key, dimension in DIMENSIONS.items())
    return int((value / 2).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fake_vector(case: str) -> tuple[dict, bool]:
    """Return the fixed synthetic TTL vector used by the independent fixture."""
    ttl = "mixed" if case.startswith("mixed") else case
    r.require(ttl in {"total-only", "5m", "1h", "mixed"}, "unknown fixture cache vector")
    cc, cc1h = (0, 80) if ttl == "1h" else ((50, 30) if ttl == "mixed" else (80, 0))
    return {"p": 1000, "c": 100, "cr": 200, "cc": cc, "cc1h": cc1h}, "-max" in case


def usage_vector(result: dict) -> dict:
    """Validate response usage independently of the saved settlement vector."""
    usage = result.get("usage")
    r.require(isinstance(usage, dict), "real response usage missing")
    if result["path"] == "/v1/messages":
        split = usage.get("cache_creation") or {}
        r.require(isinstance(split, dict), "real cache split malformed")
        one_hour = split.get("ephemeral_1h_input_tokens", 0)
        fields = {"p": usage.get("input_tokens"), "c": usage.get("output_tokens"),
                  "cr": usage.get("cache_read_input_tokens", 0),
                  "cc": split.get("ephemeral_5m_input_tokens", 0), "cc1h": one_hour}
        r.require(all(type(value) is int and value >= 0 for value in fields.values()), "real usage tokens invalid")
        total = usage.get("cache_creation_input_tokens", fields["cc"] + one_hour)
        r.require(type(total) is int and total >= one_hour, "real cache total smaller than one-hour split")
        return {"p": fields["p"], "c": fields["c"], "cr": fields["cr"],
                "cc": max(fields["cc"], total - one_hour), "cc1h": one_hour}
    details = usage.get("prompt_tokens_details") or {}
    r.require(isinstance(details, dict), "real Chat usage details malformed")
    fields = {"p": usage.get("prompt_tokens"), "c": usage.get("completion_tokens"),
              "cr": details.get("cached_tokens", 0), "cc": details.get("cache_creation_tokens", 0),
              "legacy_cc": details.get("cached_creation_tokens", 0)}
    r.require(all(type(value) is int and value >= 0 for value in fields.values()), "real usage tokens invalid")
    creation = max(fields["cc"], fields["legacy_cc"])
    r.require(fields["p"] >= fields["cr"] + creation, "real Chat prompt token semantics invalid")
    return {"p": fields["p"] - fields["cr"] - creation, "c": fields["c"], "cr": fields["cr"], "cc": creation, "cc1h": 0}


def validate_proofs(plan: dict, candidate: dict, pipeline: dict, fake: dict, real: dict) -> list:
    """Require complete independent cases tied to this source, image and plan."""
    validate_tariff(plan)
    r.validate_candidate_manifest(candidate)
    old_digest = plan_sha256(plan)
    for proof in (pipeline, fake, real):
        r.require(isinstance(proof, dict) and proof.get("image") == candidate["image"] and
                  proof.get("source_proof_sha256") == candidate["source_proof_sha256"] and
                  proof.get("plan_sha256") == old_digest, "verification image, source or plan binding changed")
    r.require(pipeline.get("success") is True and pipeline.get("production_unchanged") is True and
              pipeline.get("phase") == "verified", "complete successful isolated pipeline required")
    r.require(type(pipeline.get("started_at")) is int and type(pipeline.get("finished_at")) is int and
              0 < pipeline["started_at"] <= pipeline["finished_at"], "pipeline chronology invalid")
    r.require(fake.get("model") == r.MODEL and real.get("model") == r.MODEL and
              fake.get("billing_exact") is True and fake.get("compatibility_verified") is True and
              real.get("billing_exact") is True, "exact-model compatibility and billing proofs missing")
    fake_results, real_results = fake.get("results"), real.get("results")
    r.require(isinstance(fake_results, list) and len(fake_results) == len(FAKE_CASES) + len(INVALID_PATHS), "fixture case coverage incomplete")
    r.require(isinstance(real_results, list) and len(real_results) == len(REAL_CASES), "retained real case coverage incomplete")
    fake_seen, invalid_seen, vectors = Counter(), Counter(), []
    for result in fake_results:
        r.require(isinstance(result, dict), "fixture case malformed")
        if result.get("case") == "invalid":
            r.require(result.get("http") == 400 and type(result.get("quota")) is int and result["quota"] == 0 and
                      type(result.get("upstream_posts")) is int and result["upstream_posts"] == 0,
                      "invalid request was submitted, charged or not clearly rejected")
            invalid_seen[result.get("path")] += 1
            continue
        key = (result.get("path"), result.get("case"))
        r.require(key in FAKE_CASES and result.get("http") == 200 and result.get("billing_exact") is True and
                  type(result.get("upstream_posts")) is int and result["upstream_posts"] == 1, "fixture contract failed")
        params, maximum = fake_vector(key[1])
        quota = expected_quota(plan, params, maximum)
        r.require(type(result.get("quota")) is int and result["quota"] == quota, "fixture exact price vector mismatch")
        fake_seen[key] += 1
        vectors.append({"path": key[0], "case": key[1], "params": params, "quota": quota})
    r.require(fake_seen == Counter(FAKE_CASES) and invalid_seen == Counter(INVALID_PATHS), "fixture cases duplicated or missing")
    real_seen = Counter()
    for result in real_results:
        r.require(isinstance(result, dict), "real case malformed")
        key = (result.get("source"), result.get("path"), result.get("effort"))
        r.require(key in REAL_CASES and result.get("http") == 200 and result.get("response_model") == r.MODEL and
                  result.get("billing_exact") is True and result.get("uncertain") is False, "real exact-model case failed or ambiguous")
        params = usage_vector(result)
        r.require(params == result.get("tiered_token_params") and params["c"] > 0 and
                  type(result.get("quota")) is int and result["quota"] == expected_quota(plan, params, key[2] == "max"),
                  "real usage or exact price vector mismatch")
        real_seen[key] += 1
    r.require(real_seen == Counter(REAL_CASES), "real cases duplicated or missing")
    return vectors


def require_options(options: dict, plan: dict) -> None:
    """Require current canary DB/runtime values to match all six exact entries."""
    try:
        r.require(isinstance(options, dict) and Decimal(str(json.loads(options["GroupRatio"])["文"])) == Decimal("0.15"),
                  "current canary group ratio changed")
        for key, expected in r.option_updates(plan).items():
            actual = json.loads(options[key]).get(r.MODEL)
            r.require(actual == expected, "current canary pricing incomplete or changed")
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise r.RolloutError("current canary price options malformed") from None


def verify_runtime(o, candidate: dict, plan: dict, opener) -> dict:
    """Read only the isolated runtime and database; never contact an upstream."""
    info = o.h.inspect(r.CANARY_NAME)
    r.require(info.get("State", {}).get("Running") is True and info.get("Image") == candidate["image"], "wrong current canary runtime image")
    values = o.h.env(info)
    r.require(urllib.parse.urlsplit(values["SQL_DSN"]).path == "/" + r.CANARY_DB and
              not values.get("LOG_SQL_DSN") and not values.get("REDIS_CONN_STRING") and "CHANNEL_UPDATE_FREQUENCY" not in values,
              "canary database or external-state isolation changed")
    expected_env = {"QUOTA_DB_AUTHORITATIVE": "true", "BATCH_UPDATE_ENABLED": "false", "NODE_TYPE": "slave",
                    "MEMORY_CACHE_ENABLED": "false", "SQL_MAX_OPEN_CONNS": "5", "SQL_MAX_IDLE_CONNS": "1", "SQL_MAX_LIFETIME": "60"}
    r.require(all(values.get(key) == value for key, value in expected_env.items()), "canary isolation settings changed")
    require_options(o.options(), plan)
    admins = o.rows("SELECT id,trim(access_token) AS token FROM users WHERE id=1 AND role=100 AND status=1")
    r.require(len(admins) == 1 and bool(admins[0].get("token")), "canary admin read access missing")
    request = urllib.request.Request(o.h.address(r.CANARY_NAME, 3000) + "/api/option/",
                                     headers={"Authorization": "Bearer " + admins[0]["token"], "New-Api-User": str(admins[0]["id"])})
    try:
        with opener(request, timeout=15) as response:
            body = json.load(response)
    except Exception:
        raise r.RolloutError("canary runtime price read failed") from None
    r.require(body.get("success") is True and isinstance(body.get("data"), list), "canary runtime options unavailable")
    options = {row["key"]: row["value"] for row in body["data"]}
    r.require(len(options) == len(body["data"]), "canary runtime option keys duplicated")
    require_options(options, plan)
    return {"at": int(time.time()), "image": candidate["image"], "model": r.MODEL, "group_ratio": "0.15",
            "dsn_sha256": hashlib.sha256(values["SQL_DSN"].encode()).hexdigest(),
            "verified_option_keys": list(r.option_updates(plan))}


def replace_plan(path: Path, plan: dict, expected_sha256: str) -> None:
    """Atomically replace only the backed-up private task-owned plan."""
    descriptor, temporary = tempfile.mkstemp(prefix=".plan-finalize-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        r.require(hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256, "plan changed before atomic promotion")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def finalize(o=None, opener=urllib.request.urlopen) -> dict:
    """Fail closed before promoting the compatibility flag and activation proof."""
    o = r.Rollout(db=r.CANARY_DB) if o is None else o
    r.require(o.db == r.CANARY_DB, "finalization is isolated-canary-only")
    o.validation_scope(True)
    root = Path(o.root)
    for name in ("canary-verified.json", "finalize-canary-started.json", "plan-before-canary-verification.json"):
        r.require(not (root / name).exists(), "finalization already completed or interrupted; reconcile private evidence")
    plan = o.read("plan.json")
    r.require(plan.get("compatibility_verified") is False, "unpromoted candidate plan required")
    candidate = o.load_candidate()
    proofs = [o.read(name) for name in PROOF_NAMES]
    vectors = validate_proofs(plan, candidate, *proofs)
    saved = o.read(r.CANARY_DB + "-stage.json")
    r.require(saved.get("validation_only") is True and saved.get("image") == candidate["image"] and
              saved.get("plan_sha256") == plan_sha256(plan) and saved.get("updates") == r.option_updates(plan),
              "current canary stage differs from verified candidate plan")
    names = ("plan.json", "runtime-candidate.json", *PROOF_NAMES, r.CANARY_DB + "-stage.json")
    objects = dict(zip(names, (plan, candidate, *proofs, saved)))
    hashes = {}
    for name in names:
        raw = (root / name).read_bytes()
        r.require(json.loads(raw) == objects[name], "private evidence changed before digest binding")
        hashes[name] = hashlib.sha256(raw).hexdigest()
    runtime = verify_runtime(o, candidate, plan, opener)
    r.require(saved.get("dsn_sha256") == runtime["dsn_sha256"], "canary staged database authority changed")
    r.require(o.load_candidate() == candidate and o.h.inspect(r.CANARY_NAME).get("Image") == candidate["image"], "candidate changed during finalization")
    r.require(all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items()),
              "private evidence changed during finalization")
    promoted = {**plan, "compatibility_verified": True}
    result = {"at": int(time.time()), "model": r.MODEL, "image": candidate["image"],
              "plan_sha256": plan_sha256(promoted), "verified_candidate_plan_sha256": plan_sha256(plan),
              "source_proof_sha256": candidate["source_proof_sha256"], "runtime_candidate_sha256": hashes["runtime-candidate.json"],
              "compatibility_verified": True, "billing_exact": True, "runtime_pricing_verified": True,
              "deployment_blocker_resolved": True, "runtime_pricing": runtime, "billing_vectors": vectors,
              "evidence_sha256": {name: hashes[name] for name in PROOF_NAMES}}
    r.private_write(root / "finalize-canary-started.json", {"at": result["at"], "image": candidate["image"], "plan_sha256": plan_sha256(plan)})
    r.private_write(root / "plan-before-canary-verification.json", plan)
    replace_plan(root / "plan.json", promoted, hashes["plan.json"])
    r.private_write(root / "canary-verified.json", result)
    return result


if __name__ == "__main__":
    try:
        proof = finalize()
        print(json.dumps({"completed": True, "model": proof["model"], "image": proof["image"], "plan_sha256": proof["plan_sha256"]}))
    except Exception as error:
        print(json.dumps({"completed": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, r.RolloutError) else "canary finalization failed; inspect private evidence"}))
        raise SystemExit(1) from None
