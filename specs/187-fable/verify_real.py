"""Single-submit bounded real Fable checks only through the isolated runtime."""

from decimal import Decimal, ROUND_HALF_UP
import importlib.util
import hashlib
import json
from pathlib import Path
import time

import requests


ROOT = Path("/opt/ai-api-stack/backups/fable187-20261007")
spec = importlib.util.spec_from_file_location("rollout", "/opt/ai-api-stack/releases/issue187-fable/rollout.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def verify() -> None:
    """Verify each provider's native default/max and primary Chat compatibility."""
    o = r.Rollout(db=r.CANARY_DB)
    o.validation_scope(True)
    candidate = o.load_candidate()
    candidate_image = candidate["image"]
    plan_sha = hashlib.sha256(json.dumps(o.load_validation_plan(), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    r.require(o.h.inspect(r.CANARY_NAME)["Image"] == candidate_image, "real runtime is not attested candidate")
    account = o.read("canary-account.json")
    base = o.h.address(r.CANARY_NAME, 3000)
    headers = {"Authorization": "Bearer " + account["key"], "anthropic-version": "2023-06-01"}
    routes = o.rows("SELECT id,name,base_url,key FROM channels WHERE name LIKE 'Claude187:%' ORDER BY id")
    results = []
    cases = [(source, path, effort) for source in ["rolldek-ccmax", "maolao"]
             for path in ["/v1/messages"] for effort in ["", "max"]]
    cases += [("rolldek-ccmax", "/v1/chat/completions", effort) for effort in ["", "max"]]
    for source, path, effort in cases:
        route = [row for row in routes if row["name"] == "Claude187:" + source + ":" + r.MODEL]
        r.require(len(route) == 1, "exact real route missing")
        route = route[0]
        ids = ",".join(str(row["id"]) for row in routes)
        o.sql("BEGIN; UPDATE channels SET status=2 WHERE id IN (" + ids + "); UPDATE abilities SET enabled=false WHERE channel_id IN (" + ids + "); UPDATE channels SET status=1 WHERE id=" + str(route["id"]) + "; UPDATE abilities SET enabled=true WHERE channel_id=" + str(route["id"]) + "; COMMIT;")
        image = o.h.inspect(r.CANARY_NAME)["Image"]
        r.require(image == candidate_image, "real runtime changed during verification")
        marker = ROOT / (source + "-canary-" + ("messages" if path.endswith("messages") else "chat") + "-" + (effort or "default") + "-" + image.split(":")[-1][:12] + ".json")
        r.require(not marker.exists(), "real request already recorded; reconcile, never replay")
        body = {"model": r.MODEL, "messages": [{"role": "user", "content": "Reply exactly OK. No explanation."}], "max_tokens": 512}
        if effort:
            body["output_config" if path.endswith("messages") else "reasoning_effort"] = {"effort": effort} if path.endswith("messages") else effort
        before = o.rows("SELECT quota FROM users WHERE id=" + str(account["user_id"]))[0]["quota"]
        r.private_write(marker, {"started_at": int(time.time()), "source": source, "path": path, "effort": effort, "uncertain": True, "wallet_before": before, "image": image})
        started = time.monotonic()
        response = requests.post(base + path, headers=headers, json=body, timeout=(10, 60))
        data = response.json()
        result = {"source": source, "path": path, "effort": effort, "http": response.status_code,
                  "response_model": data.get("model"), "usage": data.get("usage"),
                  "seconds": round(time.monotonic() - started, 3), "uncertain": response.status_code >= 500}
        # Preserve status/content before asserting, so an interruption cannot replay.
        marker.with_suffix(".result.json").write_text(json.dumps(result, ensure_ascii=False))
        marker.with_suffix(".result.json").chmod(0o600)
        r.require(response.status_code == 200 and data.get("model") == r.MODEL, "real exact-model request failed")
        for _ in range(40):
            bills = o.rows("SELECT id,quota,other FROM logs WHERE user_id=" + str(account["user_id"]) + " AND type=2 ORDER BY id DESC LIMIT 1")
            after = o.rows("SELECT quota FROM users WHERE id=" + str(account["user_id"]))[0]["quota"]
            if bills and after < before:
                break
            time.sleep(.1)
        other = json.loads(bills[0]["other"]) if isinstance(bills[0]["other"], str) else bills[0]["other"]
        usage = data["usage"]
        if path.endswith("messages"):
            split = usage.get("cache_creation") or {}
            one_hour = split.get("ephemeral_1h_input_tokens", 0)
            five_minute = max(split.get("ephemeral_5m_input_tokens", 0), usage.get("cache_creation_input_tokens", 0) - one_hour)
            params = {"p": usage["input_tokens"], "c": usage["output_tokens"], "cr": usage.get("cache_read_input_tokens", 0), "cc": five_minute, "cc1h": one_hour}
        else:
            details = usage.get("prompt_tokens_details") or {}
            cached = details.get("cached_tokens", 0)
            creation = max(details.get("cache_creation_tokens", 0), details.get("cached_creation_tokens", 0))
            params = {"p": usage["prompt_tokens"] - cached - creation, "c": usage["completion_tokens"], "cr": cached, "cc": creation, "cc1h": 0}
        coefficients = {"p": "20.085", "c": "100.425", "cr": "0.502125", "cc": "25.10625", "cc1h": "40.17"}
        value = sum(Decimal(str(params.get(key, 0))) * Decimal(coefficient) for key, coefficient in coefficients.items())
        expected = int((value * (3 if effort == "max" else 1) / 2).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        r.require(expected == bills[0]["quota"] == before - after, "real wallet/log tariff mismatch")
        result.update(quota=expected, billing_exact=True, tiered_token_params=params)
        r.private_write(marker.with_suffix(".verified.json"), result)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    o.sql("BEGIN; UPDATE channels SET status=1 WHERE name LIKE 'Claude187:%'; UPDATE abilities SET enabled=true WHERE model=" + r.quote(r.MODEL) + "; COMMIT;")
    r.private_write(ROOT / "real-verified.json", {"model": r.MODEL, "image": candidate_image, "source_proof_sha256": candidate["source_proof_sha256"], "plan_sha256": plan_sha, "results": results, "billing_exact": True})


if __name__ == "__main__":
    verify()
