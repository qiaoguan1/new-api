#!/usr/bin/env python3
"""Idempotently make ledger atomic writes private without replacing live code."""

import argparse
from pathlib import Path


PRIVATE_WRITE = '''    os.chmod(temporary, 0o600)
    if hasattr(os, "geteuid") and os.geteuid() == 0 and pathlib.Path(path).exists():
        current = pathlib.Path(path).stat()
        os.chown(temporary, current.st_uid, current.st_gid)
'''
ANCHOR = '''    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, path)
'''
REPLACEMENT = ANCHOR.replace(
    "    os.replace(temporary, path)\n", PRIVATE_WRITE + "    os.replace(temporary, path)\n"
)


class PatchError(RuntimeError):
    """Raised when the reviewed ledger writer has drifted."""


def patch_source(source: str) -> str:
    """Insert a private mode before replace while retaining every other line."""
    if "os.chown(temporary, current.st_uid, current.st_gid)" in source:
        return source
    count = source.count(ANCHOR)
    if count != 1:
        raise PatchError(f"expected one reviewed ledger writer, found {count}")
    return source.replace(ANCHOR, REPLACEMENT, 1)


def main(argv=None) -> int:
    """Patch a source file atomically while preserving its mode."""
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
