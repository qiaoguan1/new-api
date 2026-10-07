"""Swap only the task-owned isolated runtime after an attested code revision."""

import importlib.util
import json
from pathlib import Path
import subprocess
import time
import urllib.parse

import requests


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")
spec = importlib.util.spec_from_file_location("rollout", R / "rollout.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


if __name__ == "__main__":
    o = m.Rollout(db=m.CANARY_DB)
    o.validation_scope(True)
    candidate = o.load_candidate()
    before = o.h.inspect(m.CANARY_NAME)
    env = o.h.env(before)
    assert urllib.parse.urlsplit(env["SQL_DSN"]).path == "/" + m.CANARY_DB
    assert not env.get("LOG_SQL_DSN") and not env.get("REDIS_CONN_STRING")
    m.private_write(B / "canary-runtime-before-http400.json", before)
    subprocess.run(["docker", "rm", "-f", m.CANARY_NAME], check=True, stdout=subprocess.DEVNULL)
    o.h.run(m.CANARY_NAME, candidate["image"], env, extra=["--memory", "768m"])
    for _ in range(40):
        try:
            if requests.get(o.h.address(m.CANARY_NAME, 3000) + "/api/status", timeout=2).json().get("success"):
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("refreshed canary not ready")
    stage = B / "xtai_fable187_canary-stage.json"
    saved = json.loads(stage.read_text())
    m.private_write(B / "canary-stage-before-http400.json", saved)
    saved["image"] = candidate["image"]
    stage.write_text(json.dumps(saved, ensure_ascii=False))
    stage.chmod(0o600)
    # Only the clone's exact Fable rows switch back to the private free fixture.
    o.sql("BEGIN; UPDATE channels SET status=1,base_url='http://xtai-fable187-fake:19087',key='fixture-only-no-account' WHERE name LIKE 'Claude187:%'; UPDATE abilities SET enabled=true WHERE model=" + m.quote(m.MODEL) + "; COMMIT;")
    print("only cloned runtime updated to attested HTTP400 candidate", flush=True)
