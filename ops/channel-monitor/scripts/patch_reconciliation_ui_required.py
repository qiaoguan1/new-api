#!/usr/bin/env python3
"""Update a deployed reconciliation summary to required/optional semantics."""

import argparse
import os
import re
from pathlib import Path


PATTERN = re.compile(
    r"^\s*<span>.*\$\{fmtInt\(totals\.complete_upstreams\)\}.*\$\{fmtInt\(totals\.expected_upstreams\)\}.*$\n"
    r"^\s*<span>.*\$\{fmtInt\(totals\.incomplete_upstreams\)\}.*$\n"
    r"^\s*<span>.*\$\{fmtInt\(totals\.credentialless_upstreams\)\}.*$",
    re.MULTILINE,
)

REPLACEMENT = """    <span>必需完整 ${fmtInt(totals.complete_required_upstreams)} / ${fmtInt(totals.required_upstreams)} 家</span>
    <span>必需未完成 ${fmtInt(totals.incomplete_required_upstreams)} 家</span>
    <span>非必需 ${fmtInt(totals.optional_upstreams)} 家</span>"""


def transform(source):
    updated, count = PATTERN.subn(REPLACEMENT, source, count=1)
    if count != 1:
        raise RuntimeError(f"expected one legacy reconciliation summary, found {count}")
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    updated = transform(args.target.read_text(encoding="utf-8"))
    if not args.check:
        temporary = args.target.with_name(args.target.name + ".required.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(f"required/optional reconciliation UI patch verified: write={not args.check}")


if __name__ == "__main__":
    main()
