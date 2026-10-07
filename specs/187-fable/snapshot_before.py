"""Private read-only preservation snapshot before the scoped Fable addition."""

import importlib.util
from pathlib import Path
import json
import time


R = Path("/opt/ai-api-stack/releases/issue187-fable")


if __name__ == "__main__":
    sp = importlib.util.spec_from_file_location("rollout", R / "rollout.py")
    r = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(r)
    o = r.Rollout()
    r.require(o.image()["Image"] == r.BASELINE_IMAGE, "preservation snapshot requires original runtime")
    r.require(not any(o.snapshot_routes()), "Fable production rows already exist")
    channels = o.rows("SELECT * FROM channels ORDER BY id")
    snapshot = {"at": int(time.time()), "channels": channels,
                "abilities": o.rows("SELECT * FROM abilities ORDER BY channel_id,model,\"group\""),
                "models": o.rows("SELECT * FROM models ORDER BY id"),
                "options": {row["key"]: row["value"] for row in o.rows("SELECT key,value FROM options")}}
    r.private_write(o.root / "production-config-before.json", snapshot)
    print(json.dumps({"preservation_snapshot": True, "old_channels": len(channels), "keys_not_printed": True}))
