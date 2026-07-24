#!/usr/bin/env python3
"""Make explicit CompletionRatio options authoritative over family defaults."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


OLD_GET = '''func GetCompletionRatio(name string) float64 {
\tname = FormatMatchingModelName(name)

\tif strings.Contains(name, "/") {
\t\tif ratio, ok := completionRatioMap.Get(name); ok {
\t\t\treturn ratio
\t\t}
\t}
\thardCodedRatio, contain := getHardcodedCompletionModelRatio(name)
\tif contain {
\t\treturn hardCodedRatio
\t}
\tif ratio, ok := completionRatioMap.Get(name); ok {
\t\treturn ratio
\t}
\treturn hardCodedRatio
}
'''

NEW_GET = '''func GetCompletionRatio(name string) float64 {
\tname = FormatMatchingModelName(name)

\t// An explicit database option is authoritative for every model. Family
\t// defaults remain fallbacks for models without an operator-managed value.
\tif ratio, ok := completionRatioMap.Get(name); ok {
\t\treturn ratio
\t}
\thardCodedRatio, contain := getHardcodedCompletionModelRatio(name)
\tif contain {
\t\treturn hardCodedRatio
\t}
\treturn hardCodedRatio
}
'''

OLD_INFO = '''func GetCompletionRatioInfo(name string) CompletionRatioInfo {
\tname = FormatMatchingModelName(name)

\tif strings.Contains(name, "/") {
\t\tif ratio, ok := completionRatioMap.Get(name); ok {
\t\t\treturn CompletionRatioInfo{
\t\t\t\tRatio:  ratio,
\t\t\t\tLocked: false,
\t\t\t}
\t\t}
\t}

\thardCodedRatio, locked := getHardcodedCompletionModelRatio(name)
\tif locked {
\t\treturn CompletionRatioInfo{
\t\t\tRatio:  hardCodedRatio,
\t\t\tLocked: true,
\t\t}
\t}

\tif ratio, ok := completionRatioMap.Get(name); ok {
\t\treturn CompletionRatioInfo{
\t\t\tRatio:  ratio,
\t\t\tLocked: false,
\t\t}
\t}

\treturn CompletionRatioInfo{
\t\tRatio:  hardCodedRatio,
\t\tLocked: false,
\t}
}
'''

NEW_INFO = '''func GetCompletionRatioInfo(name string) CompletionRatioInfo {
\tname = FormatMatchingModelName(name)

\tif ratio, ok := completionRatioMap.Get(name); ok {
\t\treturn CompletionRatioInfo{
\t\t\tRatio:  ratio,
\t\t\tLocked: false,
\t\t}
\t}

\thardCodedRatio, locked := getHardcodedCompletionModelRatio(name)
\tif locked {
\t\treturn CompletionRatioInfo{
\t\t\tRatio:  hardCodedRatio,
\t\t\tLocked: true,
\t\t}
\t}

\treturn CompletionRatioInfo{
\t\tRatio:  hardCodedRatio,
\t\tLocked: false,
\t}
}
'''


def patch_text(source: str) -> tuple[str, bool]:
    if NEW_GET in source and NEW_INFO in source:
        return source, False
    updated = source
    for old, new, label in (
        (OLD_GET, NEW_GET, "completion ratio getter"),
        (OLD_INFO, NEW_INFO, "completion ratio metadata getter"),
    ):
        count = updated.count(old)
        if count != 1:
            raise ValueError(f"expected exactly one {label}, found {count}")
        updated = updated.replace(old, new, 1)
    return updated, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    updated, changed = patch_text(source)
    if changed:
        temporary = args.target.with_name(f".{args.target.name}.issue30.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode)
        os.replace(temporary, args.target)
        print(f"patched {args.target}")
    else:
        print(f"already patched {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
