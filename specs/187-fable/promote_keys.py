"""Promote task-owned Fable keys once; later runs only refresh read evidence.

An uncertain PUT is not replayed. Token reveal is a read-only POST endpoint,
not a generation call. Credentials and complete token backups remain private.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import time

import requests


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")
CONFIG = Path("/opt/ai-api-stack/channel-monitor")
MODEL = "claude-fable-5-1"
SOURCES = {"rolldek-ccmax": ("https://rolldek.com", "https://rolldek.com", "ccmax"),
           "maolao": ("https://maolaoapi.com", "https://api.maolaoapi.com", "group_4")}
IP = "156.239.3.210"


class PromotionError(RuntimeError):
    """Safe diagnostic without passwords, keys or raw upstream responses."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PromotionError(reason)


def key_hash(raw: str) -> str:
    """Fingerprint a canonical nonmasked key without returning it to output."""
    require(isinstance(raw, str) and bool(raw) and "*" not in raw, "live key unavailable or masked")
    return hashlib.sha256((raw if raw.startswith("sk-") else "sk-" + raw).encode()).hexdigest()


def validate_secret(source: str, secret: dict) -> None:
    """Allow only the previously approved origins, groups and exact model."""
    require(source in SOURCES, "unapproved provider")
    origin, api, group = SOURCES[source]
    require((secret.get("origin"), secret.get("api_origin"), secret.get("group")) == (origin, api, group),
            "task-owned credential origin or group changed")
    require(type(secret.get("token_id")) is int and secret["token_id"] > 0 and
            isinstance(secret.get("name"), str) and bool(secret["name"]), "task-owned token identity missing")
    key_hash(secret.get("key"))


def validate_identity(token: dict, secret: dict) -> None:
    """Never alter a different ID/name, paused key, group or model scope."""
    require(type(token.get("id")) is int and token["id"] == secret["token_id"] and token.get("name") == secret["name"] and
            token.get("group") == secret["group"] and type(token.get("status")) is int and token["status"] == 1 and
            token.get("model_limits_enabled") is True and token.get("model_limits") == MODEL and
            token.get("allow_ips") == IP and token.get("cross_group_retry") is False,
            "live task-owned token identity or scope changed")


def promoted(token: dict) -> bool:
    """Return true only for the exact previously authorized production scope."""
    return type(token.get("expired_time")) is int and token["expired_time"] == -1 and token.get("unlimited_quota") is True


def promotion_body(token: dict) -> dict:
    """Retain existing fields and change only production expiry/quota mode."""
    keys = ("id", "name", "expired_time", "remain_quota", "unlimited_quota", "model_limits_enabled",
            "model_limits", "allow_ips", "group", "cross_group_retry")
    body = {key: token[key] for key in keys if key in token}
    body.update(expired_time=-1, unlimited_quota=True)
    return body


def refresh_private_proof(path: Path, value: dict) -> None:
    """Atomically refresh a proof using an exclusive 0600 temporary file."""
    require(not path.is_symlink() and (not path.exists() or path.is_file()), "proof destination unsafe")
    require(os.name == "nt" or not path.exists() or path.stat().st_mode & 0o077 == 0, "proof permissions unsafe")
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class KeyPromoter:
    """Single-submit promotion with strict live-secret and replay verification."""
    def __init__(self, rollout, probe, root: Path = B, writer=None):
        self.rollout, self.probe, self.root = rollout, probe, Path(root)
        self.writer = writer or probe.reserve_marker

    def live_token(self, session, secret: dict) -> dict:
        matches = [row for row in self.probe.token_records(session, secret["origin"]) if row["id"] == secret["token_id"]]
        require(len(matches) == 1, "live task-owned token missing or ambiguous")
        token = matches[0]
        validate_identity(token, secret)
        raw = token.get("key")
        if not isinstance(raw, str) or not raw or "*" in raw:
            response = session.post(secret["origin"] + "/api/token/" + str(secret["token_id"]) + "/key",
                                    timeout=15, allow_redirects=False)
            require(response.status_code == 200, "live key reveal read rejected")
            data = response.json()
            require(data.get("success") is True, "live key reveal read rejected")
            data = data.get("data")
            raw = data.get("key") if isinstance(data, dict) else data
        require(key_hash(raw) == key_hash(secret["key"]), "live key differs from verified secret")
        return token

    def promote(self, session, source: str, secret: dict) -> dict:
        """Already-promoted or confirmed uncertain success never submits PUT."""
        validate_secret(source, secret)
        token = self.live_token(session, secret)
        backup = self.root / (source + "-token-before-promotion.json")
        marker = self.root / (source + "-promotion-started.json")
        require(not backup.is_symlink() and not marker.is_symlink(), "promotion evidence path unsafe")
        identity = {"source": source, "token_id": secret["token_id"], "key_sha256": key_hash(secret["key"])}
        if marker.exists():
            previous = self.rollout.read(marker.name)
            require(all(previous.get(key) == value for key, value in identity.items()), "promotion marker identity changed")
            require(backup.is_file() and promoted(token), "uncertain promotion unresolved; do not repeat PUT")
        if not promoted(token):
            require(not marker.exists(), "promotion already attempted; reconcile instead of replay")
            self.probe.validate_probe_token(token, secret["group"], float(secret["cny_per_credit"]))
            if backup.exists():
                saved = self.rollout.read(backup.name)
                require(saved.get("id") == token["id"] and saved.get("name") == token["name"] and
                        saved.get("group") == token["group"], "promotion backup identity changed")
            else:
                self.writer(backup, token)
            self.writer(marker, {**identity, "at": int(time.time()), "uncertain_until_verified": True})
            response = session.put(secret["origin"] + "/api/token/", json=promotion_body(token), timeout=15, allow_redirects=False)
            require(response.status_code == 200 and response.json().get("success") is True, "promotion request rejected or unresolved")
            token = self.live_token(session, secret)
            require(promoted(token), "promotion live state incomplete; do not replay")
        proof = {**identity, "group": secret["group"], "model": MODEL, "allow_ips": IP, "status": 1,
                 "expired_time": -1, "unlimited_quota": True, "cross_group_retry": False,
                 "verified_at": int(time.time())}
        refresh_private_proof(self.root / (source + "-production-key-verified.json"), proof)
        return {"source": source, "verified_production_key": True, "token_id": secret["token_id"]}


def main() -> None:
    """Verify image/True-plan proofs before authenticating to approved sources."""
    rollout_spec = importlib.util.spec_from_file_location("rollout", R / "rollout.py")
    require(rollout_spec is not None and rollout_spec.loader is not None, "rollout helper unavailable")
    rollout = importlib.util.module_from_spec(rollout_spec)
    rollout_spec.loader.exec_module(rollout)
    o = rollout.Rollout()
    candidate, plan = o.load_candidate(), o.load_plan()
    finalized = o.read("canary-verified.json")
    require(finalized.get("model") == MODEL and finalized.get("compatibility_verified") is True and
            finalized.get("runtime_pricing_verified") is True and finalized.get("billing_exact") is True,
            "complete exact-model canary verification required")
    require(finalized.get("image") == candidate["image"] and finalized.get("source_proof_sha256") == candidate["source_proof_sha256"] and
            finalized.get("plan_sha256") == hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
            "canary image, source or True-plan binding changed")
    for evidence_name in ("fake-verified.json", "real-verified.json"):
        evidence = o.read(evidence_name)
        require(evidence.get("billing_exact") is True and evidence.get("image") == candidate["image"] and
                evidence.get("source_proof_sha256") == candidate["source_proof_sha256"], "verification image or source proof changed")
    sys.path[:0] = [str(CONFIG), str(CONFIG / "scripts")]
    sp = importlib.util.spec_from_file_location("balance", CONFIG / "scripts/fetch-upstream-balance.py")
    helper = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(helper)
    sp = importlib.util.spec_from_file_location("probe", R / "probe.py")
    probe = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(probe)
    credentials = json.loads((CONFIG / "upstream-credentials.json").read_text())
    # The whole promotion and freshness pass share one task lock. HTTP retries
    # are not configured; uncertain writes must be resolved by live readback.
    import fcntl
    descriptor = os.open(B / "promotion.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        promoter = KeyPromoter(o, probe, writer=rollout.private_write)
        for source in SOURCES:
            promote_source(promoter, helper, credentials, o, source)


def promote_source(promoter: KeyPromoter, helper, credentials: dict, o, source: str) -> None:
    """Login/logout only the selected source, never print raw exceptions."""
    slug = "rolldek" if source.startswith("rolldek") else source
    secret = o.read(source + "-secret.json")
    validate_secret(source, secret)
    session = requests.Session()
    try:
        helper.standard_login(session, secret["origin"], credentials[slug]["username"], credentials[slug]["password"])
        print(json.dumps(promoter.promote(session, source, secret)), flush=True)
    finally:
        try:
            helper.standard_logout(session, secret["origin"])
        except Exception:
            pass
        finally:
            session.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"verified_production_key": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, PromotionError) else "inspect private promotion evidence; do not repeat uncertain PUT"}))
        raise SystemExit(1) from None
