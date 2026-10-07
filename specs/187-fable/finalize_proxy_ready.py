"""Verify an already-ready intended proxy and release only its owned DRAIN.

Never restarts a service, changes compose/configuration or restores a database.
This recovery is for the verified null-vs-empty DNS comparison only.
"""

import importlib.util
import json
import os
from pathlib import Path
import time


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")


if __name__ == "__main__":
    import fcntl
    sp = importlib.util.spec_from_file_location("proxy", R / "proxy_mount.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    descriptor = os.open(B / "proxy-maintain.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        o = m.ProxyMountMaintenance()
        before = o.runtime.rollout.read("proxy-mount-before.json")
        started = o.runtime.rollout.read("proxy-mount-started.json")
        recovery = o.runtime.rollout.read("proxy-mount-recovery-required.json")
        canary = o.runtime.rollout.read("proxy-mount-canary-verified.json")
        m.require(recovery.get("owned_drain_retained") is True and recovery.get("no_automatic_replay") is True and
                  recovery.get("graceful_shutdown_pending") is not True, "recovery is not the verified ready-proxy case")
        m.require(canary.get("configuration_exact") is True and canary.get("nginx_image") == m.NGINX_IMAGE,
                  "isolated proxy configuration was not verified")
        m.require(not (B / "proxy-mount-ready.json").exists() and not (B / "proxy-mount-verified.json").exists(),
                  "proxy readiness already recorded; reconcile rather than replay")
        m.check_resolved_compose(before["resolved_compose"], o.resolve_compose())
        main = m.STATIC_MAIN.read_text()
        m.require(main == m.static_main(before["main"]), "intended pinned main config changed")
        o.ready(before, main, directory=True)
        info = m.DRAIN.stat()
        m.require(not m.DRAIN.is_symlink() and info.st_uid == 0 and info.st_mode & 0o077 == 0 and
                  started["at"] <= info.st_mtime <= recovery["at"] + 1 and m.DRAIN.read_text() == m.DRAIN_OWNER,
                  "retained image drain ownership or timing changed")
        marker = {"at": int(time.time()), "directory_ro": True, "main_include_pinned": True,
                  "no_service_restart": True, "configuration_verified": True}
        m.private_write(B / "proxy-mount-ready.json", marker)
        again = m.DRAIN.stat()
        m.require((again.st_dev, again.st_ino, again.st_mtime_ns) == (info.st_dev, info.st_ino, info.st_mtime_ns) and
                  not m.DRAIN.is_symlink() and m.DRAIN.read_text() == m.DRAIN_OWNER, "retained drain changed before release")
        m.DRAIN.unlink()
        m.private_write(B / "proxy-mount-verified.json", {**marker, "nginx_image": m.NGINX_IMAGE,
                        "loaded_configuration_exact": True, "dormant_configs_unloaded": True,
                        "native_gateway_unchanged": True, "public_status_http": 200, "owned_drain_released": True})
        print(json.dumps({"proxy_ready_verified": True, "owned_drain_released": True, "no_service_restart": True}))
