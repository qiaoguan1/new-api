"""Archive one proven no-swap cancellation, retaining its fence until durable.

This script neither repairs nginx mounts nor reruns a deployment. A partial
archive requires reconciliation; never remove the remaining runtime fence.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")
ARCHIVE = "runtime-cancelled-attempt-1"
RECEIPT = "runtime-resume-1.json"
PHASES = ("runtime-before.json", "runtime-aborted.json", "runtime-started.json")


class ResumeError(RuntimeError):
    """Credential-free no-replay precondition failure."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ResumeError(reason)


def validate_cancellation(cancelled: dict, before: dict, started: dict, baseline: str) -> None:
    """Bind cancellation to the known baseline and strict no-swap assertions."""
    require(cancelled.get("binary_switch_attempted") is False and cancelled.get("ingress_released") is True,
            "cancelled attempt is not proven no-swap; manual reconciliation required")
    require(before.get("native_image") == baseline and started.get("baseline_image") == baseline,
            "cancelled evidence has wrong baseline")
    require(type(started.get("at")) is int and type(cancelled.get("at")) is int and
            started["at"] <= cancelled["at"], "cancelled evidence time ordering is invalid")


def archive_plan(root: Path, release: Path) -> tuple[Path, list[Path], Path]:
    """Validate every source/destination before creating a directory or moving."""
    root, release = Path(root), Path(release)
    archive, receipt = root / ARCHIVE, root / RECEIPT
    require(not root.is_symlink() and root.is_dir() and not release.is_symlink() and release.is_dir(),
            "task evidence roots are unsafe or missing")
    require(not archive.exists() and not archive.is_symlink() and
            not receipt.exists() and not receipt.is_symlink(), "cancelled archive or resume receipt already exists")
    require(all(not (root / name).exists() and not (root / name).is_symlink()
                for name in ("runtime-ready.json", "runtime-deployed.json", "runtime-recovery-required.json")),
            "runtime recovery ambiguity")
    files = [root / PHASES[0], root / PHASES[1], release / "runtime-deploy.log", root / PHASES[2]]
    require(all(path.is_file() and not path.is_symlink() for path in files), "cancelled task evidence incomplete")
    require(os.name == "nt" or root.stat().st_mode & 0o077 == 0, "task evidence directory permissions unsafe")
    return archive, files, receipt


def fsync_directory(path: Path) -> None:
    """Make rename ordering durable on the Linux deployment host."""
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def archive_cancelled(root: Path, release: Path, writer, rename=None) -> None:
    """Keep the replay fence until all evidence and its receipt are durable."""
    archive, files, receipt = archive_plan(root, release)
    identities = {path: (path.stat().st_dev, path.stat().st_ino) for path in files}
    archive.mkdir(mode=0o700)
    move = rename or (lambda source, destination: source.rename(destination))
    for source in files[:-1]:
        require(not source.is_symlink() and (source.stat().st_dev, source.stat().st_ino) == identities[source] and
                not (archive / source.name).exists() and not (archive / source.name).is_symlink(),
                "cancelled evidence changed; retain replay fence")
        move(source, archive / source.name)
        fsync_directory(source.parent)
        fsync_directory(archive)
    writer(receipt, {"at": int(time.time()), "baseline_verified": True, "ingress_restored": True,
                    "no_binary_swap": True, "archive": archive.name, "replay_fence_released_last": True})
    fsync_directory(root)
    fence = files[-1]
    require(not fence.is_symlink() and (fence.stat().st_dev, fence.stat().st_ino) == identities[fence] and
            not (archive / fence.name).exists() and not (archive / fence.name).is_symlink(),
            "cancelled replay fence changed; do not retry")
    move(fence, archive / fence.name)
    fsync_directory(root)
    fsync_directory(archive)


def prepare_resume() -> None:
    """Validate restored baseline state, then archive only this cancelled task."""
    import fcntl  # Server-only; offline Windows imports remain side-effect free.
    require(not (B / "runtime-deploy.lock").is_symlink(), "task lock path unsafe")
    descriptor = os.open(B / "runtime-deploy.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sp = importlib.util.spec_from_file_location("deploy", R / "deploy_runtime.py")
        require(sp is not None and sp.loader is not None, "runtime helper unavailable")
        d = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(d)
        archive_plan(B, R)
        o = d.RuntimeDeployment()
        cancelled, before, started = (o.rollout.read(name) for name in
                                      ("runtime-aborted.json", "runtime-before.json", "runtime-started.json"))
        validate_cancellation(cancelled, before, started, d.BASELINE_IMAGE)
        info = o.h.inspect(o.h.NATIVE)
        d.check_native(info, o.h.env(info), d.BASELINE_IMAGE, before["native_env"], set(before["native_networks"]))
        require(not d.COMPOSE.is_symlink() and not d.NGINX.is_symlink() and
                d.COMPOSE.read_text() == before["compose"] and d.NGINX.read_text() == before["nginx"] and
                not d.DRAIN.exists() and not d.DRAIN.is_symlink(), "ingress or baseline configuration not restored")
        # Do not repair/recreate a single-file read-only bind. Its container-side
        # baseline must be restored independently of the visible host file.
        loaded = subprocess.run(["docker", "exec", d.NGINX_CONTAINER, "cat", "/etc/nginx/conf.d/default.conf"],
                                capture_output=True, text=True, timeout=15)
        require(loaded.returncode == 0 and loaded.stdout == before["nginx"], "container nginx baseline is not restored")
        checked = subprocess.run(["docker", "exec", d.NGINX_CONTAINER, "nginx", "-t"],
                                 capture_output=True, timeout=15)
        require(checked.returncode == 0, "container nginx configuration check failed")
        archive_cancelled(B, R, d.private_write)
        print(json.dumps({"prepared_retry": True, "baseline_verified": True, "archived_cancelled_attempt": 1}))


if __name__ == "__main__":
    try:
        prepare_resume()
    except Exception as error:
        print(json.dumps({"prepared_retry": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, ResumeError) else "inspect private cancelled evidence; do not replay"}))
        raise SystemExit(1) from None
