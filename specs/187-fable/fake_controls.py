"""Point only cloned Fable routes at the non-paying fixture, or restore them."""

import importlib.util
from pathlib import Path
import subprocess
import sys


R = Path("/opt/ai-api-stack/releases/issue187-fable")
spec = importlib.util.spec_from_file_location("rollout", R / "rollout.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


if __name__ == "__main__":
    o = m.Rollout(db=m.CANARY_DB)
    o.validation_scope(True)
    before = o.root / "canary-real-routes-before-fake.json"
    if sys.argv[1] == "start":
        routes = o.rows("SELECT id,name,base_url,key FROM channels WHERE name LIKE 'Claude187:%'")
        assert len(routes) == 2
        m.private_write(before, routes)
        (R / "fake_upstream.py").chmod(0o644)
        image = o.h.inspect("xtai-image-job-gateway-image-job-gateway-1")["Config"]["Image"]
        subprocess.run(["docker", "run", "-d", "--name", "xtai-fable187-fake", "--network", "app-net", "--memory", "64m",
                        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=1m", "-v", str(R / "fake_upstream.py") + ":/app.py:ro",
                        "--entrypoint", "python", image, "/app.py"], check=True, stdout=subprocess.DEVNULL)
        ids = ",".join(str(row["id"]) for row in routes)
        o.sql("UPDATE channels SET base_url='http://xtai-fable187-fake:19087',key='fixture-only-no-account' WHERE id IN (" + ids + ");")
        print("private fake enabled only in cloned Fable routes", flush=True)
    elif sys.argv[1] == "restore":
        routes = o.read(before.name)
        statements = ["BEGIN;"]
        for row in routes:
            statements.append("UPDATE channels SET base_url=" + m.quote(row["base_url"]) + ",key=" + m.quote(row["key"]) +
                              " WHERE id=" + str(row["id"]) + " AND name=" + m.quote(row["name"]) + ";")
        o.sql("\n".join(statements + ["COMMIT;"]))
        print("cloned real routes restored; production unchanged", flush=True)
    else:
        raise ValueError("unknown fixture phase")
