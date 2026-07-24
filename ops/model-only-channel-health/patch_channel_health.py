#!/usr/bin/env python3
"""Atomically replace the legacy production channel-health page with a safe view."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


REQUIRED_LEGACY_MARKERS = (
    "api.get<MonitorData>('/api/channel-monitor')",
    "渠道收入、真实成本与毛利",
    "各上游模型真实成本",
    "上游运行排行",
    "每 5 分钟刷新",
    "模型性能（近 {hours} 小时）",
)

FORBIDDEN_SAFE_MARKERS = (
    "/api/channel-monitor",
    "gross_profit",
    "gross_margin",
    "upstream_name",
    "channel_name",
    "上游运行排行",
    "每 5 分钟刷新",
)


def validate_safe(source: str) -> None:
    missing = [
        marker
        for marker in (
            "/api/perf-metrics/summary?hours=${hours}",
            "每小时汇总，可手动刷新。",
            "<TableHead>模型</TableHead>",
            "<TableHead className='text-right'>成功率</TableHead>",
        )
        if marker not in source
    ]
    forbidden = [marker for marker in FORBIDDEN_SAFE_MARKERS if marker in source]
    if missing or forbidden:
        raise ValueError(f"unsafe template: missing={missing}, forbidden={forbidden}")


def patch_text(source: str, replacement: str) -> tuple[str, bool]:
    validate_safe(replacement)
    if source == replacement:
        return source, False
    missing = [marker for marker in REQUIRED_LEGACY_MARKERS if marker not in source]
    if missing:
        raise ValueError(f"unknown legacy page shape; missing markers: {missing}")
    return replacement, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).with_name("sanitized_channel_monitor.tsx"),
    )
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    replacement = args.template.read_text(encoding="utf-8")
    updated, changed = patch_text(source, replacement)
    if changed:
        temporary = args.target.with_name(f".{args.target.name}.issue32.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode)
        os.replace(temporary, args.target)
        print(f"patched {args.target}")
    else:
        print(f"already patched {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
