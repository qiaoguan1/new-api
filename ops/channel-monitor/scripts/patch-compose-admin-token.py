#!/usr/bin/env python3
"""Idempotently mount the private Nginx admin-token include read-only."""

import argparse
from pathlib import Path


MOUNT = "./nginx/auth/channel-monitor-token.inc:/etc/nginx/auth/channel-monitor-token.inc:ro"
ANCHOR = "      - ./nginx/auth/channel-monitor.htpasswd:/etc/nginx/auth/channel-monitor.htpasswd:ro\n"
REPLACEMENT = ANCHOR + f"      - {MOUNT}\n"


class PatchError(RuntimeError):
    """Raised when the reviewed Compose volume list has drifted."""


def patch_source(source: str) -> str:
    """Add the secret include mount exactly once or fail closed."""
    if MOUNT in source:
        return source
    count = source.count(ANCHOR)
    if count != 1:
        raise PatchError(f"expected one reviewed htpasswd mount, found {count}")
    return source.replace(ANCHOR, REPLACEMENT, 1)


def main(argv=None) -> int:
    """Patch a Compose file atomically while preserving its mode."""
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
