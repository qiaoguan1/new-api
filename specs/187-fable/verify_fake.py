"""Non-paying full-path Fable validation on the isolated database only."""

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
    """Check wire parameters, exact wallet/log deltas and rejected no-POST cases."""
    rollout = r.Rollout(db=r.CANARY_DB)
    rollout.validation_scope(True)
    image = rollout.h.inspect(r.CANARY_NAME)["Image"]
    candidate = rollout.load_candidate()
    plan_sha = hashlib.sha256(json.dumps(rollout.load_validation_plan(), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    r.require(image == candidate["image"], "fixture runtime is not attested candidate")
    account = rollout.read("canary-account.json")
    base = rollout.h.address(r.CANARY_NAME, 3000)
    fake = rollout.h.address("xtai-fable187-fake", 19087)
    headers = {"Authorization": "Bearer " + account["key"], "anthropic-version": "2023-06-01"}
    uid = account["user_id"]
    tid = account["token_id"]
    # Synthetic quota exists only in the clone and never funds an upstream key.
    rollout.sql("BEGIN; UPDATE users SET quota=5000000 WHERE id=" + str(uid) + "; UPDATE tokens SET remain_quota=5000000 WHERE id=" + str(tid) + "; COMMIT;")
    cases = [(path, ttl, max_mode, stream) for path in ["/v1/messages", "/v1/chat/completions"]
             for ttl in ["total-only", "5m", "1h", "mixed"]
             for max_mode, stream in [(False, False)]]
    cases += [(path, "mixed", True, stream) for path in ["/v1/messages", "/v1/chat/completions"] for stream in [False, True]]
    results = []
    for index, (path, ttl, max_mode, stream) in enumerate(cases):
        before = rollout.rows("SELECT quota FROM users WHERE id=" + str(uid))[0]["quota"]
        posts_before = requests.get(fake, timeout=5).json()["posts"]
        case = ttl + ("-max" if max_mode else "") + ("-stream" if stream else "")
        body = {"model": r.MODEL, "messages": [{"role": "user", "content": "FABLE_CASE:" + case}], "max_tokens": 512, "stream": stream}
        if max_mode:
            if path == "/v1/messages":
                body["output_config"] = {"effort": "max"}
            else:
                body["reasoning_effort"] = "max"
        response = requests.post(base + path, headers=headers, json=body, timeout=20)
        r.require(response.status_code == 200, "private fixture request failed")
        r.require("OK" in response.text, "private fixture content missing")
        for _ in range(30):
            bills = rollout.rows("SELECT quota,other FROM logs WHERE user_id=" + str(uid) + " AND type=2 ORDER BY id DESC LIMIT 1")
            after = rollout.rows("SELECT quota FROM users WHERE id=" + str(uid))[0]["quota"]
            if bills and after < before:
                break
            time.sleep(0.1)
        cc, cc1h = (0, 80) if ttl == "1h" else ((50, 30) if ttl == "mixed" else (80, 0))
        cny_m = (Decimal(1000) * Decimal("20.085") + Decimal(100) * Decimal("100.425") +
                 Decimal(200) * Decimal("0.502125") + Decimal(cc) * Decimal("25.10625") + Decimal(cc1h) * Decimal("40.17"))
        expected = int((cny_m * (3 if max_mode else 1) / 2).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        r.require(before - after == expected and bills[0]["quota"] == expected, "fixture wallet/log tariff mismatch")
        captured = requests.get(fake, timeout=5).json()
        r.require(captured["posts"] == posts_before + 1, "private fixture duplicated submission")
        wire = captured["records"][-1]
        r.require(wire["thinking"].get("type") == "adaptive" and "budget_tokens" not in wire["thinking"], "wrong Fable thinking wire contract")
        if max_mode:
            r.require(wire["output_config"].get("effort") == "max", "max effort not actually forwarded")
        results.append({"path": path, "case": case, "http": 200, "quota": expected, "billing_exact": True, "upstream_posts": 1})
    invalid = [
        ("/v1/chat/completions", {"output_config": {"effort": "max"}}),
        ("/v1/messages", {"thinking": {"type": "disabled"}}),
        ("/v1/messages", {"max_tokens_to_sample": 4096}),
        ("/v1/chat/completions", {"max_tokens": 128001}),
        ("/v1/responses", {"input": "FABLE_CASE:invalid", "reasoning": {"effort": "max"}}),
    ]
    for path, extra in invalid:
        before = rollout.rows("SELECT quota FROM users WHERE id=" + str(uid))[0]["quota"]
        posts = requests.get(fake, timeout=5).json()["posts"]
        body = {"model": r.MODEL, "messages": [{"role": "user", "content": "FABLE_CASE:invalid"}], "max_tokens": 512, **extra}
        response = requests.post(base + path, headers=headers, json=body, timeout=20)
        r.require(response.status_code == 400, "invalid Fable request did not fail before generation")
        after = rollout.rows("SELECT quota FROM users WHERE id=" + str(uid))[0]["quota"]
        r.require(after == before and requests.get(fake, timeout=5).json()["posts"] == posts, "invalid request charged or submitted")
        results.append({"path": path, "case": "invalid", "http": 400, "quota": 0, "upstream_posts": 0})
    r.require(rollout.h.inspect(r.CANARY_NAME)["Image"] == image, "fixture runtime changed during verification")
    r.private_write(ROOT / "fake-verified.json", {"model": r.MODEL, "image": image, "source_proof_sha256": candidate["source_proof_sha256"], "plan_sha256": plan_sha, "results": results,
                                                  "billing_exact": True, "compatibility_verified": True})
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    verify()
