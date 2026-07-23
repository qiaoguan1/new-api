#!/usr/bin/env python3
"""Apply reviewed follow-up fixes to an already patched monitor app.js."""

import argparse
import os
from pathlib import Path


SUMMARY = '    <span>本站 ${fmtCny(totals.local_billed_cny, 6)}</span>'
UNASSIGNED = '    <span>本站调用 ${fmtInt(totals.local_calls)}（未归属 ${fmtInt(totals.unassigned_local_calls)}）</span>'


def transform(source):
    if source.count(SUMMARY) != 1:
        raise RuntimeError(f"expected one local billing summary, found {source.count(SUMMARY)}")
    if UNASSIGNED in source:
        raise RuntimeError("unassigned summary already exists")
    updated = source.replace(SUMMARY, SUMMARY + "\n" + UNASSIGNED, 1)
    if 'row.actual_log_complete && row.last_attempt_status === "incomplete"' not in updated:
        raise RuntimeError("safe retry-warning condition is missing")
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    updated = transform(args.target.read_text(encoding="utf-8"))
    temporary = args.target.with_name(args.target.name + ".followup.tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, args.target.stat().st_mode & 0o777)
    os.replace(temporary, args.target)
    print("reconciliation UI follow-up applied")


if __name__ == "__main__":
    main()
