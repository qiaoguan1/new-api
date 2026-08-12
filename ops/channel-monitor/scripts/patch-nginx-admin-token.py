#!/usr/bin/env python3
"""Idempotently inject the private admin token include into the Nginx route."""

import argparse
from pathlib import Path


INCLUDE = "include /etc/nginx/auth/channel-monitor-token.inc;"
ANCHOR = """        auth_basic \"Channel Monitor\";
        auth_basic_user_file /etc/nginx/auth/channel-monitor.htpasswd;
        proxy_pass http://172.18.0.1:8791/channel-monitor/admin/;
"""
REPLACEMENT = """        auth_basic \"Channel Monitor\";
        auth_basic_user_file /etc/nginx/auth/channel-monitor.htpasswd;
        include /etc/nginx/auth/channel-monitor-token.inc;
        proxy_pass http://172.18.0.1:8791/channel-monitor/admin/;
"""


class PatchError(RuntimeError):
    """Raised when the reviewed Nginx location has drifted."""


def patch_source(source: str) -> str:
    """Add the include exactly once or fail closed on an unknown layout."""
    if INCLUDE in source:
        return source
    count = source.count(ANCHOR)
    if count != 1:
        raise PatchError(f"expected one reviewed admin location, found {count}")
    return source.replace(ANCHOR, REPLACEMENT, 1)


def main(argv=None) -> int:
    """Patch a configuration file atomically while preserving its mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    original = args.path.read_text(encoding="utf-8")
    patched = patch_source(original)
    if patched != original:
        temporary = args.path.with_suffix(args.path.suffix + ".issue24.tmp")
        temporary.write_text(patched, encoding="utf-8")
        temporary.chmod(args.path.stat().st_mode & 0o777)
        temporary.replace(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
