"""Durable candidate-only build, safe to observe after a desktop SSH disconnect."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


ROOT = Path("/opt/ai-api-stack/releases/issue187-fable")


if __name__ == "__main__":
    proof = ROOT / "source-amended.json"
    digest = hashlib.sha256(proof.read_bytes()).hexdigest()
    marker = ROOT / ("build-result-" + digest[:12] + ".json")
    try:
        text = (ROOT / "source/Dockerfile").read_text()
        needle = "RUN go build -ldflags "
        assert text.count(needle) == 1
        text = text.replace(needle, "RUN GOMAXPROCS=2 go test -p 1 ./controller ./dto ./relay/helper ./service ./pkg/billingexpr ./relay/channel/claude\nRUN GOMAXPROCS=2 go build -p 1 -ldflags ", 1)
        (ROOT / "Dockerfile.fable187").write_text(text)
        subprocess.run(["docker", "build", "--memory", "2g", "--label", "xtai.issue187.source_sha256=" + digest,
                        "-f", str(ROOT / "Dockerfile.fable187"), "-t", "new-api-fixed:issue187-fable", str(ROOT / "source")], check=True)
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", "new-api-fixed:issue187-fable"], text=True))[0]
        assert info["Config"]["Labels"]["xtai.issue187.source_sha256"] == digest
        result = {"success": True, "image": info["Id"], "source_amended_sha256": digest, "at": int(time.time())}
    except Exception as error:
        result = {"success": False, "error_type": type(error).__name__, "source_amended_sha256": digest, "at": int(time.time())}
    with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(result, handle)
    latest = ROOT / "build-result.latest"
    with os.fdopen(os.open(latest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(result, handle)
    os.replace(latest, ROOT / "build-result.json")
    print(json.dumps(result), flush=True)
