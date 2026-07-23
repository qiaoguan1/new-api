#!/usr/bin/env python3
"""Idempotently apply the Beijing-time policy to production-only monitor files."""

import argparse
import os
import pathlib
import stat


DEFAULT_ROOT = pathlib.Path("/opt/ai-api-stack/channel-monitor")


class PatchError(RuntimeError):
    """Raised when a production source no longer matches an audited anchor."""


def replace_once(source, old, new, label):
    """Replace one audited anchor, or accept an already-patched source."""
    if old in source:
        count = source.count(old)
        if count != 1:
            raise PatchError(f"{label}: expected one legacy anchor, found {count}")
        return source.replace(old, new)
    if new in source:
        return source
    raise PatchError(f"{label}: neither legacy nor patched anchor was found")


def rename_all(source, old, new, label):
    """Rename all remaining exact references, or verify the new name exists."""
    if old in source:
        return source.replace(old, new)
    if new in source:
        return source
    raise PatchError(f"{label}: neither legacy nor patched name was found")


def patch_scan_source(source):
    """Patch the daily audit worker to use one Beijing business-day policy."""
    source = replace_once(
        source,
        "from datetime import datetime, timedelta, timezone",
        "from datetime import datetime\n\nfrom monitor_time import beijing_iso_now, resolve_beijing_business_day",
        "scan imports",
    )
    source = replace_once(
        source,
        '''def now_local():
    return datetime.now(timezone.utc).astimezone()


def now_iso():
    return now_local().isoformat(timespec="seconds")


def target_utc_day():
    """默认审计上一个完整 UTC 日；可用 CHANNEL_MONITOR_DAY=YYYY-MM-DD 覆盖。"""
    override = os.environ.get("CHANNEL_MONITOR_DAY", "").strip()
    if override:
        datetime.strptime(override, "%Y-%m-%d")
        return override
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
''',
        '''def now_iso():
    return beijing_iso_now()


def target_beijing_day():
    """默认审计上一个完整北京时间日；可用 CHANNEL_MONITOR_DAY 覆盖。"""
    return resolve_beijing_business_day(os.environ.get("CHANNEL_MONITOR_DAY", ""))
''',
        "scan time helpers",
    )
    source = rename_all(
        source, "target_utc_day()", "target_beijing_day()", "scan target day"
    )
    source = replace_once(
        source,
        "AT TIME ZONE 'UTC'",
        "AT TIME ZONE 'Asia/Shanghai'",
        "scan SQL timezone",
    )
    return source


def patch_generate_source(source):
    """Patch monitor materialization to aggregate and timestamp in Beijing."""
    source = replace_once(
        source,
        "from datetime import datetime, timezone",
        "from monitor_time import beijing_iso_now, beijing_now",
        "generator imports",
    )
    source = replace_once(
        source,
        '''def local_now():
    return datetime.now(timezone.utc).astimezone()
''',
        '''def local_now():
    return beijing_now()
''',
        "generator local clock",
    )
    source = replace_once(
        source,
        "按完整 UTC 日核算站内收入、上游人民币真实成本和渠道毛利。",
        "按完整北京时间日核算站内收入、上游人民币真实成本和渠道毛利。",
        "generator business-day documentation",
    )
    source = replace_once(
        source,
        "AT TIME ZONE 'UTC'",
        "AT TIME ZONE 'Asia/Shanghai'",
        "generator SQL timezone",
    )
    source = replace_once(
        source,
        'datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")',
        "beijing_iso_now()",
        "generator ISO timestamp",
    )
    return source


def patch_app_source(source):
    """Patch the protected monitor UI labels and epoch rendering timezone."""
    source = replace_once(
        source,
        "核对 UTC 日期：",
        "核对北京时间业务日：",
        "internal monitor date label",
    )
    source = replace_once(
        source,
        'toLocaleString("zh-CN", { hour12: false })',
        'toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })',
        "internal monitor timestamp formatter",
    )
    return source


def write_atomic(path, content):
    """Atomically write a patched source file without changing its mode."""
    original_mode = stat.S_IMODE(path.stat().st_mode)
    temporary = pathlib.Path(str(path) + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(original_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(root=DEFAULT_ROOT):
    """Validate every audited patch before writing any changed source file."""
    root = pathlib.Path(root)
    targets = (
        (root / "scripts" / "scan-upstream-daily.py", patch_scan_source),
        (root / "scripts" / "generate-monitor-data.py", patch_generate_source),
        (root / "app.js", patch_app_source),
    )
    pending = []
    for path, patcher in targets:
        source = path.read_text(encoding="utf-8")
        patched = patcher(source)
        if patched != source:
            pending.append((path, patched))

    for path, patched in pending:
        write_atomic(path, patched)
    return [str(path) for path, _ in pending]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    for path in apply(args.root):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
