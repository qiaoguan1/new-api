#!/usr/bin/env python3
"""Remove credential identifiers from the fetch worker's structured output."""

import argparse
import ast
import os
from pathlib import Path


OLD = """            "slug": slug, "status": status, "website": origin,
            "username": username, "group": group,
"""
NEW = """            "slug": slug, "status": status, "website": origin,
            "group": group,
"""


def transform(source):
    count = source.count(OLD)
    if count != 1:
        raise RuntimeError(f"expected one username output block, found {count}")
    updated = source.replace(OLD, NEW, 1)
    ast.parse(updated)
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    updated = transform(args.target.read_text(encoding="utf-8"))
    if not args.check:
        temporary = args.target.with_name(args.target.name + ".issue5.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(f"issue5 fetch patch verified: write={not args.check}")


if __name__ == "__main__":
    main()
