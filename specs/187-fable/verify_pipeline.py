"""Durable canary verification, never stages or changes production."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import time


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")


if __name__ == "__main__":
    result = {"success": False, "production_unchanged": True, "started_at": int(time.time())}
    try:
        for command in [["attest_candidate.py"], ["refresh_canary.py"], ["verify_fake.py"],
                        ["fake_controls.py", "restore"], ["verify_real.py"]]:
            result["phase"] = command[0]
            subprocess.run(["python3", "-u", str(R / command[0]), *command[1:]], check=True)
        candidate = json.loads((B / "runtime-candidate.json").read_text())
        result["image"] = candidate["image"]
        result["source_proof_sha256"] = candidate["source_proof_sha256"]
        result["plan_sha256"] = hashlib.sha256(json.dumps(json.loads((B / "plan.json").read_text()), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        result.update(success=True, phase="verified", finished_at=int(time.time()))
    except Exception as error:
        result.update(error_type=type(error).__name__, finished_at=int(time.time()))
    with os.fdopen(os.open(B / "validation-pipeline-result.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(result, handle)
    print(json.dumps(result), flush=True)
