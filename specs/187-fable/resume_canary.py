"""Reconcile the proven rolled-back first staging transaction in the clone."""

import importlib.util
from pathlib import Path
import secrets
import subprocess
import time

import requests


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")
spec = importlib.util.spec_from_file_location("rollout", R / "rollout.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


if __name__ == "__main__":
    o = m.Rollout(db=m.CANARY_DB)
    o.validation_scope(True)
    assert o.rows("SELECT count(*) AS n FROM channels WHERE name LIKE 'Claude187:%'")[0]["n"] == 0
    assert o.rows("SELECT count(*) AS n FROM models WHERE model_name='claude-fable-5-1' AND deleted_at IS NULL")[0]["n"] == 0
    archive = B / "clone-stage-rolledback-first"
    archive.mkdir(mode=0o700)
    for name in ["xtai_fable187_canary-stage-started.json", "xtai_fable187_canary-before.json"]:
        path = B / name
        assert path.exists()
        path.rename(archive / name)
    o._stage(validation=True)
    info = o.image()
    candidate = o.load_candidate()
    env = m.canary_environment(o.h.env(info))
    inspected = subprocess.run(["docker", "inspect", m.CANARY_NAME], capture_output=True)
    assert inspected.returncode != 0
    o.h.run(m.CANARY_NAME, candidate["image"], env, extra=["--memory", "768m"])
    uid = int(o.sql('INSERT INTO users(username,password,role,status,quota,used_quota,request_count,"group",aff_code,created_at) '
                    "VALUES('fable187-canary','!nonlogin-test-account',1,1,5000000,0,0,'default'," + m.quote(secrets.token_hex(5)) +
                    "," + str(int(time.time())) + ") RETURNING id;").splitlines()[0])
    key = secrets.token_hex(24)
    tid = int(o.sql('INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) '
                    "VALUES(" + str(uid) + "," + m.quote(key) + ",1,'fable187-canary'," + str(int(time.time())) + ",0," +
                    str(int(time.time()) + 3600) + ",5000000,false,true," + m.quote(m.MODEL) + ",'',0,'auto',false) RETURNING id;").splitlines()[0])
    m.private_write(B / "canary-account.json", {"user_id": uid, "token_id": tid, "key": "sk-" + key, "initial_quota": 5000000})
    for _ in range(40):
        try:
            if requests.get(o.h.address(m.CANARY_NAME, 3000) + "/api/status", timeout=2).json().get("success"):
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("canary not ready")
    o.activate_validation()
    m.private_write(B / "xtai_fable187_canary-prepare-validation.json", {"database": m.CANARY_DB, "image": candidate["image"],
                                                                         "reconciled_failed_stage": True, "production_unchanged": True})
    print("isolated candidate ready", uid, tid, flush=True)
