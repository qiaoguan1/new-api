"""GET-only fresh retained-source tariff check before production price staging.

Existing authorized login/logout may authenticate the pricing GET. This script
never generates content, changes a key, derives a recharge conversion or edits
the plan. An unavailable source is isolated, but both must pass to create the
exclusive freshcost-proof.json. Reconcile/archive an old proof before rerunning.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time


MODEL = "claude-fable-5-1"
ROOT = Path("/opt/ai-api-stack/backups/fable187-20261007")
RELEASE = Path("/opt/ai-api-stack/releases/issue187-fable")
CONFIG = Path("/opt/ai-api-stack/channel-monitor")
SOURCES = {"rolldek-ccmax": ("https://rolldek.com", "https://rolldek.com", "ccmax"),
           "maolao": ("https://maolaoapi.com", "https://api.maolaoapi.com", "group_4")}
NUMERIC_FIELDS = frozenset({"model_ratio", "completion_ratio", "cache_ratio", "create_cache_ratio",
                            "model_price", "quota_type", "image_ratio", "audio_ratio", "audio_completion_ratio",
                            "ModelRatio", "CompletionRatio", "CacheRatio", "CreateCacheRatio", "ModelPrice"})
TEXT_FIELDS = frozenset({"billing_mode", "billing_expr", "pricing_version", "currency", "billing_unit"})
COST_PATTERN = re.compile(r"price|ratio|billing|cost|cache", re.IGNORECASE)


class CostError(RuntimeError):
    """Static credential-free tariff precondition failure."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CostError(reason)


def digest(value: object) -> str:
    """Hash canonical evidence without exposing its contents."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def number(value: object, positive: bool = False) -> str:
    """Normalize only finite nonnegative numeric values, never infer units."""
    require(type(value) in (int, float, str), "cost numeric field has unsupported type")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CostError("cost numeric field is malformed") from None
    require(parsed.is_finite() and (parsed > 0 if positive else parsed >= 0), "cost numeric field is out of range")
    return format(parsed.normalize(), "f")


def project_contract(pricing: dict, source: str) -> dict:
    """Pin exact-model cost fields and effective selected-group ratio.

    Unknown cost-looking fields fail closed rather than inventing their meaning.
    Optional known fields must retain identical presence/value in the new row.
    Description, endpoints, unrelated groups and vendor labels are not costs.
    """
    require(source in SOURCES, "provider outside retained source scope")
    require(isinstance(pricing, dict) and pricing.get("success") is True and isinstance(pricing.get("data"), list),
            "pricing response rejected or malformed")
    rows = [row for row in pricing["data"] if isinstance(row, dict) and row.get("model_name") == MODEL]
    require(len(rows) == 1, "exact Fable pricing row missing or ambiguous")
    row, group = rows[0], SOURCES[source][2]
    require(isinstance(row.get("enable_groups"), list) and group in row["enable_groups"],
            "exact model is not enabled in retained upstream group")
    require(row.get("billing_mode") == "tiered_expr" and isinstance(row.get("billing_expr"), str) and
            bool(row["billing_expr"].strip()) and "\x00" not in row["billing_expr"], "existing expression cost binding missing")
    unsupported = [key for key in row if COST_PATTERN.search(key) and key not in NUMERIC_FIELDS | TEXT_FIELDS]
    require(not unsupported, "unsupported cost contract field requires review")
    fields = {key: number(value) for key, value in row.items() if key in NUMERIC_FIELDS}
    for key in TEXT_FIELDS & row.keys():
        require(isinstance(row[key], str) and bool(row[key].strip()) and "\x00" not in row[key],
                "cost text field is malformed")
        fields[key] = row[key]
    ratios = pricing.get("group_ratio")
    require(isinstance(ratios, dict) and group in ratios, "effective selected-group ratio missing")
    return {"model": MODEL, "group": group, "group_ratio": number(ratios[group], positive=True), "cost_fields": fields}


def compare_contract(source: str, saved: dict, live: dict, secret: dict, credential: dict) -> dict:
    """Require unchanged captured contract and approved recharge conversion."""
    require(source in SOURCES and isinstance(secret, dict) and isinstance(credential, dict), "retained credential evidence missing")
    origin, api, group = SOURCES[source]
    require((secret.get("origin"), secret.get("api_origin"), secret.get("group")) == (origin, api, group),
            "retained credential origin or group changed")
    conversion = number(secret.get("cny_per_credit"), positive=True)
    require(conversion == number(credential.get("rate"), positive=True), "approved recharge conversion changed")
    before, current = project_contract(saved, source), project_contract(live, source)
    require(before == current, "upstream exact-model cost contract changed")
    return {"source": source, "group": group, "group_ratio": current["group_ratio"], "cny_per_credit": conversion,
            "cost_fields": current["cost_fields"], "baseline_contract_sha256": digest(before),
            "live_contract_sha256": digest(current), "baseline_evidence_sha256": digest(saved),
            "live_evidence_sha256": digest(live), "verified_at": int(time.time()), "transport": "authenticated_get"}


def validate_binding(final: dict, plan: dict, candidate: dict) -> None:
    """Bind fresh evidence to the already verified True plan and immutable build."""
    require(isinstance(plan, dict) and plan.get("model") == MODEL and plan.get("compatibility_verified") is True,
            "verified exact-model True plan required")
    require([(item.get("source"), item.get("priority")) for item in plan.get("providers", [])] ==
            [("rolldek-ccmax", 10), ("maolao", 8)], "retained provider plan changed")
    require(isinstance(final, dict) and all(final.get(key) is True for key in
            ("compatibility_verified", "billing_exact", "runtime_pricing_verified")) and
            final.get("model") == MODEL and final.get("plan_sha256") == digest(plan) and
            final.get("image") == candidate.get("image") and
            final.get("source_proof_sha256") == candidate.get("source_proof_sha256"), "final canary source or True-plan binding changed")


def validate_fresh_cost_proof(proof: dict, plan: dict, candidate: dict, now: int | None = None) -> None:
    """Fail closed on incomplete/stale proof before any production price write."""
    current = int(time.time()) if now is None else now
    require(isinstance(proof, dict) and proof.get("success") is True and proof.get("cost_contract_verified") is True and
            proof.get("model") == MODEL and proof.get("plan_sha256") == digest(plan) and
            proof.get("image") == candidate.get("image") and
            proof.get("source_proof_sha256") == candidate.get("source_proof_sha256"), "fresh cost proof binding incomplete")
    sources = proof.get("sources")
    require(isinstance(sources, list) and len(sources) == 2 and {row.get("source") for row in sources} == set(SOURCES),
            "fresh cost proof retained-source coverage incomplete")
    for timestamp in [proof.get("verified_at"), *(row.get("verified_at") for row in sources)]:
        require(type(timestamp) is int and 0 <= current - timestamp <= 300, "fresh cost proof is stale or future dated")
    for row in sources:
        require(row.get("group") == SOURCES[row["source"]][2] and isinstance(row.get("cost_fields"), dict) and bool(row["cost_fields"]) and
                row.get("baseline_contract_sha256") == row.get("live_contract_sha256") and
                re.fullmatch(r"[0-9a-f]{64}", row.get("live_contract_sha256", "")) is not None,
                "fresh cost source contract proof incomplete")
        projection = {"model": MODEL, "group": row["group"], "group_ratio": number(row.get("group_ratio"), positive=True),
                      "cost_fields": row["cost_fields"]}
        require(digest(projection) == row["live_contract_sha256"], "fresh cost projected contract hash changed")
        number(row.get("cny_per_credit"), positive=True)
        for key in ("baseline_evidence_sha256", "live_evidence_sha256"):
            require(isinstance(row.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", row[key]) is not None,
                    "fresh cost evidence hash missing")


def private_write(path: Path, value: object) -> None:
    """Create exclusive durable task evidence; never overwrite prior proofs."""
    require(path.parent.is_dir() and not path.parent.is_symlink(), "private cost evidence root missing or unsafe")
    require(os.name == "nt" or path.parent.stat().st_mode & 0o077 == 0, "private cost evidence root permissions unsafe")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def fetch_pricing(source: str, credential: dict, helper, session_factory) -> dict:
    """Use existing approved login then only a no-redirect pricing GET."""
    require(source in SOURCES, "provider outside retained source scope")
    origin = SOURCES[source][0]
    session = session_factory()
    session.trust_env = False
    try:
        helper.standard_login(session, origin, credential["username"], credential["password"])
        response = session.get(origin + "/api/pricing", timeout=20, allow_redirects=False)
        require(response.status_code == 200, "authorized pricing GET unavailable")
        return response.json()
    finally:
        try:
            helper.standard_logout(session, origin)
        except Exception:
            pass
        session.close()


class CostRecheck:
    """Isolate source failures while requiring complete cost coverage to pass."""
    def __init__(self, rollout, credentials: dict, fetch, root: Path = ROOT, writer=private_write):
        self.rollout, self.credentials, self.fetch, self.root, self.writer = rollout, credentials, fetch, Path(root), writer

    def run(self) -> dict:
        """Record sanitized success or partial failure without editing the plan."""
        report = {"success": False, "cost_contract_verified": False, "model": MODEL, "started_at": int(time.time()),
                  "sources": [], "failures": [], "generation_posts": 0, "key_mutations": 0, "price_mutations": 0}
        try:
            destination = self.root / "freshcost-proof.json"
            require(not destination.exists() and not destination.is_symlink(), "existing fresh cost proof requires reconciliation")
            plan, candidate = self.rollout.load_plan(), self.rollout.load_candidate()
            validate_binding(self.rollout.read("canary-verified.json"), plan, candidate)
            report.update(image=candidate["image"], source_proof_sha256=candidate["source_proof_sha256"], plan_sha256=digest(plan))
            for source in SOURCES:
                try:
                    credential = self.credentials["rolldek" if source == "rolldek-ccmax" else source]
                    saved, key = self.rollout.read(source + "-pricing.json"), self.rollout.read(source + "-secret.json")
                    current = self.fetch(source, credential)
                    report["sources"].append(compare_contract(source, saved, current, key, credential))
                except Exception as error:
                    report["failures"].append({"source": source, "error_type": type(error).__name__,
                        "reason": str(error) if isinstance(error, CostError) else "authorized source cost evidence unavailable"})
            report.update(verified_at=int(time.time()), success=not report["failures"], cost_contract_verified=not report["failures"])
            if report["success"]:
                validate_fresh_cost_proof(report, plan, candidate)
        except Exception as error:
            report.update(success=False, cost_contract_verified=False, error_type=type(error).__name__,
                          reason=str(error) if isinstance(error, CostError) else "private cost evidence prerequisite failed")
        output = self.root / ("freshcost-proof.json" if report["success"] else "cost-recheck-failed-" + secrets.token_hex(8) + ".json")
        self.writer(output, report)
        return report


def main() -> None:
    """Load server-local authorized helpers only when explicitly executed."""
    import requests
    spec = importlib.util.spec_from_file_location("fable_cost_rollout", RELEASE / "rollout.py")
    require(spec is not None and spec.loader is not None, "rollout helper missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rollout = module.Rollout()
    sys.path[:0] = [str(CONFIG), str(CONFIG / "scripts")]
    spec = importlib.util.spec_from_file_location("fable_cost_login", CONFIG / "scripts/fetch-upstream-balance.py")
    require(spec is not None and spec.loader is not None, "authorized login helper missing")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    credentials = json.loads((CONFIG / "upstream-credentials.json").read_text())
    report = CostRecheck(rollout, credentials, lambda source, credential: fetch_pricing(source, credential, helper, requests.Session)).run()
    print(json.dumps({"success": report["success"], "cost_contract_verified": report["cost_contract_verified"],
                      "model": MODEL, "verified_sources": [row["source"] for row in report["sources"]],
                      "failures": report["failures"], "reason": report.get("reason"), "generation_posts": 0}, ensure_ascii=False))
    raise SystemExit(0 if report["success"] else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"success": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, CostError) else "authorized cost recheck could not start", "generation_posts": 0}))
        raise SystemExit(1) from None
