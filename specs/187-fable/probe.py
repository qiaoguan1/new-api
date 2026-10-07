"""Bounded exact-model probes; credentials and full evidence stay on the server."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import requests


MODEL = "claude-fable-5-1"
ROOT = Path("/opt/ai-api-stack/channel-monitor")
OUT = Path("/opt/ai-api-stack/backups/fable187-20261007")
SOURCES = {
    "rolldek": ("https://rolldek.com", "https://rolldek.com", "claude-kiro"),
    "rolldek-ccmax": ("https://rolldek.com", "https://rolldek.com", "ccmax"),
    "maolao": ("https://maolaoapi.com", "https://api.maolaoapi.com", "group_4"),
    "nodyhub": ("https://nodyhub.com", "https://nodyhub.com", "默认通道"),
}


def private_write(path: Path, value: object) -> None:
    """Persist private evidence atomically without printing credential material."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def reserve_marker(path: Path, value: object) -> None:
    """Reserve a durable exclusive marker before any paid or ambiguous write."""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())


def validate_probe_token(token: dict, group: str, rate: float) -> None:
    """Reject expired, over-budget, cross-group or non-model-scoped credentials."""
    now = int(time.time())
    expires = token.get("expired_time", -1)
    remaining = token.get("remain_quota", -1)
    if (token.get("group") != group or token.get("status") != 1 or
            token.get("unlimited_quota") is not False or token.get("model_limits_enabled") is not True or
            token.get("model_limits") != MODEL or token.get("allow_ips") != "156.239.3.210" or
            token.get("cross_group_retry") is not False or not isinstance(expires, int) or
            not now < expires <= now + 86430 or not isinstance(remaining, int) or
            not 0 < remaining <= int(500000 / rate)):
        raise ValueError("probe_key_scope_or_budget_mismatch")


def token_records(session: requests.Session, origin: str) -> list[dict]:
    """Search a bounded paginated list instead of recreating hidden older keys."""
    records: list[dict] = []
    seen: set[int] = set()
    for page in range(1, 11):
        response = session.get(origin + "/api/token/",
                               params={"p": page, "size": 100, "page_size": 100}, timeout=15)
        response.raise_for_status()
        body = response.json()
        if body.get("success") is not True:
            raise RuntimeError("token_list_rejected")
        data = body.get("data", {})
        batch = data.get("items", []) if isinstance(data, dict) else data
        if not isinstance(batch, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), int) for r in batch):
            raise RuntimeError("invalid_token_list")
        ids = {r["id"] for r in batch}
        if len(ids) != len(batch) or seen & ids:
            raise RuntimeError("token_pagination_repeated_items")
        records.extend(batch)
        seen.update(ids)
        total = data.get("total") if isinstance(data, dict) else None
        if not batch or (isinstance(total, int) and len(records) >= total) or (total is None and len(batch) < 100):
            return records
    raise RuntimeError("token_list_exceeds_bounded_search")


def probe(slug: str, mode: str) -> None:
    """Prepare a scoped finite key, or submit a single durably recorded probe."""
    OUT.mkdir(exist_ok=True, mode=0o700)
    os.chmod(OUT, 0o700)
    sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
    spec = importlib.util.spec_from_file_location("balance", ROOT / "scripts/fetch-upstream-balance.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("missing_authorized_login_helper")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    credential_slug = "rolldek" if slug == "rolldek-ccmax" else slug
    credentials = json.loads((ROOT / "upstream-credentials.json").read_text())[credential_slug]
    origin, api_origin, group = SOURCES[slug]
    name = "xtai-fable187-probe-" + slug + "-20261007"
    result_path = OUT / (slug + "-probe.json")
    started_path = OUT / (slug + "-probe-started.json")
    if mode == "probe" and (result_path.exists() or started_path.exists()):
        raise RuntimeError("probe_already_started_reconcile_instead_of_replaying")
    session = requests.Session()
    try:
        helper.standard_login(session, origin, credentials["username"], credentials["password"])
        pricing_response = session.get(origin + "/api/pricing", timeout=20)
        pricing_response.raise_for_status()
        pricing = pricing_response.json()
        candidates = [r for r in pricing.get("data", []) if r.get("model_name") == MODEL]
        if len(candidates) != 1 or group not in candidates[0].get("enable_groups", []):
            raise RuntimeError("exact_model_not_enabled_in_selected_group")
        private_write(OUT / (slug + "-pricing.json"), pricing)
        matches = [r for r in token_records(session, origin) if r.get("name") == name]
        if not matches:
            body = {
                "name": name,
                "expired_time": int(time.time()) + 86400,
                "remain_quota": int(500000 / float(credentials["rate"])),
                "unlimited_quota": False,
                "model_limits_enabled": True,
                "model_limits": MODEL,
                "allow_ips": "156.239.3.210",
                "group": group,
                "cross_group_retry": False,
            }
            reserve_marker(OUT / (slug + "-key-create-started.json"), {"name": name, "time": int(time.time())})
            response = session.post(origin + "/api/token/", json=body, timeout=15)
            response.raise_for_status()
            if response.json().get("success") is not True:
                raise RuntimeError("probe_key_creation_rejected")
            matches = [r for r in token_records(session, origin) if r.get("name") == name]
        if len(matches) != 1:
            raise RuntimeError("ambiguous_probe_key")
        token = matches[0]
        validate_probe_token(token, group, float(credentials["rate"]))
        raw_key = token.get("key", "")
        if not raw_key or "*" in raw_key:
            response = session.post(origin + "/api/token/" + str(token["id"]) + "/key", timeout=15)
            response.raise_for_status()
            data = response.json().get("data")
            raw_key = data.get("key", "") if isinstance(data, dict) else data
        if not isinstance(raw_key, str) or not raw_key or "*" in raw_key:
            raise RuntimeError("probe_key_not_revealed")
        key = raw_key if raw_key.startswith("sk-") else "sk-" + raw_key
        private_write(OUT / (slug + "-secret.json"), {
            "token_id": token["id"], "key": key, "origin": origin,
            "api_origin": api_origin, "group": group, "name": name,
            "cny_per_credit": credentials["rate"],
        })
        print(json.dumps({"source": slug, "prepared": True, "token_id": token["id"],
                          "group": group, "maximum_probe_budget_cny": 1}, ensure_ascii=False), flush=True)
        if mode == "prepare":
            return
        payload = {"model": MODEL, "messages": [{"role": "user", "content": "Reply exactly OK. No explanation."}],
                   "max_tokens": 512, "stream": False}
        reserve_marker(started_path, {"source": slug, "started_at": int(time.time()), "request": payload})
        private_write(result_path, {"source": slug, "started_at": int(time.time()), "request": payload, "uncertain": True})
        started = time.monotonic()
        try:
            response = session.post(api_origin + "/v1/chat/completions",
                                    headers={"Authorization": "Bearer " + key}, json=payload, timeout=(10, 45))
            data = response.json()
            choices = data.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            result = {
                "source": slug, "http": response.status_code,
                "success": response.status_code == 200 and bool(message.get("content")) and data.get("model") == MODEL,
                "response_model": data.get("model"), "usage": data.get("usage"),
                "seconds": round(time.monotonic() - started, 3),
                "request_id": response.headers.get("X-Request-Id") or response.headers.get("X-Oneapi-Request-Id"),
                "uncertain": response.status_code >= 500,
            }
            private_write(OUT / (slug + "-response.json"), data)
        except Exception as error:
            result = {"source": slug, "success": False, "uncertain": True,
                      "error_type": type(error).__name__, "seconds": round(time.monotonic() - started, 3)}
        private_write(result_path, result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        logs = session.get(origin + "/api/log/self",
                           params={"p": 1, "page_size": 100, "token_name": name}, timeout=20)
        logs.raise_for_status()
        body = logs.json()
        private_write(OUT / (slug + "-logs.json"), body)
        print(json.dumps({"source": slug, "billing_log_read": body.get("success") is True}), flush=True)
    finally:
        try:
            helper.standard_logout(session, origin)
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("mode", choices=["prepare", "probe"])
    args = parser.parse_args()
    try:
        probe(args.source, args.mode)
    except Exception as error:
        print(json.dumps({"source": args.source, "completed": False,
                          "error_type": type(error).__name__,
                          "next_step": "reconcile private evidence; do not replay ambiguous writes"}), flush=True)
        raise SystemExit(1) from None
