"""Archive a proven pre-maintenance canary-order failure, fence released last."""

import importlib.util
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")
STACK = Path("/opt/ai-api-stack")
ARCHIVE = "proxy-cancelled-env-check-1"
RECEIPT = "proxy-check-reconciled-1.json"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def fsync_dir(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def archive(root: Path, release: Path, writer, move=None, *, attempt: int = 1) -> None:
    """Never clear the old started fence before the archive receipt is durable."""
    require(root.is_dir() and not root.is_symlink() and release.is_dir() and not release.is_symlink(),
            "proxy evidence roots unsafe")
    require(os.name == "nt" or root.stat().st_mode & 0o077 == 0, "proxy evidence root permissions unsafe")
    require(attempt in (1, 2), "unknown proxy check attempt")
    destination = root / (ARCHIVE if attempt == 1 else "proxy-cancelled-host-check-2")
    receipt = root / (RECEIPT if attempt == 1 else "proxy-check-reconciled-2.json")
    require(not destination.exists() and not destination.is_symlink() and
            not receipt.exists() and not receipt.is_symlink(), "existing proxy reconciliation evidence")
    static = root / "proxy-main-nginx.conf" if attempt == 1 else STACK / "nginx/issue187-main-nginx.conf"
    files = [root / "proxy-mount-before.json", static,
             release / "proxy-maintain.log", root / "proxy-mount-started.json"]
    require(all(path.is_file() and not path.is_symlink() for path in files), "proxy evidence incomplete or unsafe")
    identity = {path: (path.stat().st_dev, path.stat().st_ino) for path in files}
    destination.mkdir(mode=0o700)
    rename = move or (lambda old, new: old.rename(new))
    for path in files[:-1]:
        require(not path.is_symlink() and (path.stat().st_dev, path.stat().st_ino) == identity[path], "proxy archive source changed")
        require(not destination.is_symlink() and not (destination / path.name).exists() and not (destination / path.name).is_symlink(),
                "proxy archive destination changed")
        rename(path, destination / path.name)
        fsync_dir(path.parent)
        fsync_dir(destination)
    writer(receipt, {"at": int(time.time()), "no_live_proxy_changes": True, "original_config_verified": True,
                     "archive": destination.name, "release_fence_last": True})
    fsync_dir(root)
    fence = files[-1]
    require(not fence.is_symlink() and (fence.stat().st_dev, fence.stat().st_ino) == identity[fence], "proxy replay fence changed")
    require(not destination.is_symlink() and not (destination / fence.name).exists() and not (destination / fence.name).is_symlink(),
            "proxy fence destination changed")
    rename(fence, destination / fence.name)
    fsync_dir(root)
    fsync_dir(destination)


if __name__ == "__main__":
    import fcntl
    sp = importlib.util.spec_from_file_location("proxy", R / "proxy_mount.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    attempt = args.attempt
    descriptor = os.open(B / "proxy-maintain.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        o = m.ProxyMountMaintenance()
        require(not any((B / name).exists() or (B / name).is_symlink() for name in
                        ["proxy-mount-ready.json", "proxy-mount-verified.json", "proxy-mount-recovery-required.json",
                         "proxy-mount-aborted.json", "proxy-mount-canary-verified.json"]), "proxy phase is ambiguous")
        before = o.runtime.rollout.read("proxy-mount-before.json")
        started = o.runtime.rollout.read("proxy-mount-started.json")
        require(started.get("native_baseline") == m.BASELINE_IMAGE, "proxy baseline changed")
        result = json.loads((R / "proxy-maintain.log").read_text().strip())
        reason = "canary environment changed" if attempt == 1 else "canary DNS or filesystem settings changed"
        if attempt == 2:
            require(o.runtime.rollout.read(RECEIPT).get("no_live_proxy_changes") is True, "first check reconciliation missing")
        require(result == {"completed": False, "error_type": "ProxyMaintenanceError", "reason": reason},
                "proxy failure is not the verified pre-maintenance environment check")
        actual = o.h.inspect(m.NGINX)
        require(actual["Id"] == before["nginx_inspect"]["Id"] and actual["State"]["StartedAt"] == before["nginx_inspect"]["State"]["StartedAt"],
                "proxy restarted or changed; do not archive")
        m.check_recreated_proxy(before["nginx_inspect"], actual, directory=False)
        require(m.service_identity(o.h.inspect(m.NATIVE)) == before["native_identity"] and
                m.service_identity(o.h.inspect(m.GATEWAY)) == before["gateway_identity"], "unrelated service changed")
        require(not m.DRAIN.exists() and not m.DRAIN.is_symlink() and
                (attempt == 2 or (not m.STATIC_MAIN.exists() and not m.STATIC_MAIN.is_symlink())),
                "maintenance is not proven untouched")
        require(m.BASE.read_text() == before["base_compose"] and m.OVERRIDE.read_text() == before["override_compose"] and
                m.DEFAULT.read_text() == before["default"], "compose or default config changed")
        for target, expected in [(m.MAIN_TARGET, before["main"]), (m.FILE_TARGET, before["default"])]:
            require(o.run(["docker", "exec", m.NGINX, "cat", target]) == expected, "loaded proxy config changed")
        require(o.run(["docker", "exec", m.NGINX, "nginx", "-T"]) == before["loaded"], "effective proxy routes changed")
        static = B / "proxy-main-nginx.conf" if attempt == 1 else m.STATIC_MAIN
        require(static.is_file() and not static.is_symlink() and static.read_text() == m.static_main(before["main"]), "task-owned main evidence changed")
        require(not o.run(["docker", "ps", "-aq", "--filter", "label=xtai.issue187.proxy-check"]).strip(), "proxy canary cleanup incomplete")
        with urllib.request.urlopen("https://api.aixingtuyun.com/api/status", timeout=5) as response:
            require(response.status == 200 and json.load(response).get("success") is True, "public site unhealthy")
        archive(B, R, m.private_write, attempt=attempt)
        print(json.dumps({"reconciled": True, "no_live_changes": True, "archived_owned_failed_check": True}))
