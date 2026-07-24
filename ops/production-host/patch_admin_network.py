#!/usr/bin/env python3
"""Bind the channel administration API to the Docker bridge securely."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


IMPORT_ANCHOR = "import json\nimport subprocess\n"
PATCHED_IMPORT_ANCHOR = "import json\nimport os\nimport subprocess\n"
OLD_SERVER = """if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', 8791), Handler)
    print('channel monitor admin listening on :8791')
    server.serve_forever()
"""
NEW_SERVER = """if __name__ == '__main__':
    bind_host = os.environ.get('CHANNEL_MONITOR_ADMIN_BIND_HOST', '127.0.0.1')
    bind_port = int(os.environ.get('CHANNEL_MONITOR_ADMIN_BIND_PORT', '8791'))
    server = ThreadingHTTPServer((bind_host, bind_port), Handler)
    print(f'channel monitor admin listening on {bind_host}:{bind_port}')
    server.serve_forever()
"""
OLD_NGINX_UPSTREAM = (
    "proxy_pass http://154.12.55.120:8791/channel-monitor/admin/;"
)
NEW_NGINX_UPSTREAM = "proxy_pass http://172.18.0.1:8791/channel-monitor/admin/;"


def _atomic_write(path: Path, content: str) -> None:
    """Replace a text file atomically while preserving its permission bits."""
    current_mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, current_mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def is_admin_patched(content: str) -> bool:
    """Return whether the administration server has the secure bind contract."""
    return (
        PATCHED_IMPORT_ANCHOR in content
        and "CHANNEL_MONITOR_ADMIN_BIND_HOST" in content
        and "ThreadingHTTPServer((bind_host, bind_port), Handler)" in content
    )


def patch_admin_server(path: Path) -> bool:
    """Patch the exact production administration server, failing closed on drift."""
    content = path.read_text(encoding="utf-8")
    if is_admin_patched(content):
        return False
    if content.count(IMPORT_ANCHOR) != 1 or content.count(OLD_SERVER) != 1:
        raise ValueError("expected admin server anchors were not found exactly once")
    patched = content.replace(IMPORT_ANCHOR, PATCHED_IMPORT_ANCHOR, 1)
    patched = patched.replace(OLD_SERVER, NEW_SERVER, 1)
    _atomic_write(path, patched)
    return True


def is_nginx_patched(content: str) -> bool:
    """Return whether Nginx uses the host-side Docker bridge address."""
    return NEW_NGINX_UPSTREAM in content and OLD_NGINX_UPSTREAM not in content


def patch_nginx(path: Path) -> bool:
    """Patch the exact Nginx administration upstream, failing closed on drift."""
    content = path.read_text(encoding="utf-8")
    if is_nginx_patched(content):
        return False
    if content.count(OLD_NGINX_UPSTREAM) != 1:
        raise ValueError("expected Nginx admin upstream was not found exactly once")
    patched = content.replace(OLD_NGINX_UPSTREAM, NEW_NGINX_UPSTREAM, 1)
    _atomic_write(path, patched)
    return True


def main() -> int:
    """Patch or verify the production administration network boundary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-server", type=Path, required=True)
    parser.add_argument("--nginx-config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        admin_ok = is_admin_patched(args.admin_server.read_text(encoding="utf-8"))
        nginx_ok = is_nginx_patched(args.nginx_config.read_text(encoding="utf-8"))
        if not admin_ok or not nginx_ok:
            raise SystemExit("administration network patch is incomplete")
        print("administration network patch verified")
        return 0

    admin_changed = patch_admin_server(args.admin_server)
    nginx_changed = patch_nginx(args.nginx_config)
    print(f"admin_changed={admin_changed} nginx_changed={nginx_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
