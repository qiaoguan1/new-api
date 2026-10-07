"""Durable scoped Fable rollout after separately verified proxy maintenance.

Never retries a mutating command or generation. An interrupted phase is kept
for private reconciliation; no customer balance restoration is available.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")


def execute(command: list[str], phase: str, result: dict) -> None:
    """Execute once with durable private diagnostics and a bounded deadline."""
    result["phase"] = phase
    progress(result)
    path = B / ("finish-" + phase + ".log")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        completed = subprocess.run(["python3", "-u", str(R / command[0]), *command[1:]],
                                   stdout=handle, stderr=subprocess.STDOUT, timeout=240)
    if completed.returncode:
        raise RuntimeError("phase failed; inspect private evidence without replay")


def progress(result: dict) -> None:
    """Replace only this task-owned private progress file, not phase markers."""
    temporary = B / "finish-rollout-progress.tmp"
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(result, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, B / "finish-rollout-progress.json")


def synchronize(seconds: int, phase: str, result: dict) -> None:
    """Wait for the existing runtime synchronizer without changing its policy."""
    result["phase"] = phase
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result["sync_remaining_seconds"] = max(0, int(deadline - time.monotonic()))
        progress(result)
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    result.pop("sync_remaining_seconds", None)


if __name__ == "__main__":
    sp = importlib.util.spec_from_file_location("rollout", R / "rollout.py")
    r = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(r)
    o = r.Rollout()
    candidate = o.load_candidate()
    o.load_plan()
    # The proxy helper's completed artifact is additionally checked by root
    # before dispatch. Live mount/content/status checks remain in runtime preflight.
    r.require(not (B / "finish-rollout-started.json").exists(), "finish already started; reconcile instead of replay")
    r.require(not (B / "finish-rollout-result.json").exists(), "finish result already exists")
    result = {"success": False, "model": r.MODEL, "image": candidate["image"], "phase": "starting",
              "started_at": int(time.time()), "generation_posts": 0, "customer_balance_mutations": 0}
    r.private_write(B / "finish-rollout-started.json", result)
    try:
        proxy = o.read("proxy-mount-verified.json")
        r.require(proxy.get("directory_ro") is True and proxy.get("main_include_pinned") is True and
                  proxy.get("loaded_configuration_exact") is True and proxy.get("native_gateway_unchanged") is True,
                  "approved proxy maintenance not independently verified")
        execute(["cost_recheck.py"], "current-cost-contracts", result)
        if not (B / "production-config-before.json").exists():
            execute(["snapshot_before.py"], "preservation-snapshot", result)
        else:
            snapshot = o.read("production-config-before.json")
            r.require(bool(snapshot.get("channels")) and isinstance(snapshot.get("options"), dict), "preservation snapshot incomplete")
        execute(["resume_runtime.py"], "archive-cancelled-runtime", result)
        execute(["deploy_runtime.py", "deploy"], "runtime-deploy", result)
        runtime = o.read("runtime-deployed.json")
        r.require(runtime.get("image") == candidate["image"] and runtime.get("ingress_released") is True,
                  "candidate runtime or ingress verification incomplete")
        execute(["promote_keys.py"], "production-keys", result)
        cost_spec = importlib.util.spec_from_file_location("fresh_cost", R / "cost_recheck.py")
        cost = importlib.util.module_from_spec(cost_spec)
        cost_spec.loader.exec_module(cost)
        cost.validate_fresh_cost_proof(o.read("freshcost-proof.json"), o.load_plan(), o.load_candidate())
        execute(["rollout.py", "stage", "--db", "new-api"], "stage-disabled-routes", result)
        synchronize(70, "price-synchronization", result)
        execute(["promote_keys.py"], "refresh-production-key-proof", result)
        execute(["rollout.py", "activate", "--db", "new-api"], "activate-routes", result)
        synchronize(70, "channel-synchronization", result)
        execute(["verify_access.py"], "public-user-access", result)
        result.update(success=True, phase="verified", finished_at=int(time.time()))
    except Exception as error:
        result.update(error_type=type(error).__name__, finished_at=int(time.time()))
    progress(result)
    r.private_write(B / "finish-rollout-result.json", result)
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result["success"] else 1)
