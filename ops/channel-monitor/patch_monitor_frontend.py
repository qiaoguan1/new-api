#!/usr/bin/env python3
"""Teach the internal monitor UI about inactive/stale health states."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


APP_HEALTH_OLD = '''  error: "错误多",
  down: "无可用通道",
};
'''
APP_HEALTH_NEW = '''  error: "错误多",
  stale: "数据待更新",
  inactive: "已停用",
  down: "无可用通道",
};
'''

APP_SUMMARY_OLD = '''    ["上游站点", totals.upstreams, `${totals.enabled_channels}/${totals.channels} 个通道启用`],
    ["24h 调用", totals.calls_24h, `${totals.errors_24h} 次错误`],
    ["24h 计费", fmtUsd(totals.cost_24h_usd), "New API 日消耗"],
    ["7d 计费", fmtUsd(totals.cost_7d_usd), "New API 周消耗"],
    ["累计消耗", fmtUsd(totals.used_usd), "按渠道 used_quota 折算"],
    ["告警项", totals.alerts, "非正常状态站点"],
'''
APP_SUMMARY_NEW = '''    ["上游站点", totals.upstreams, `${totals.enabled_channels}/${totals.channels} 个通道启用`],
    ["监控覆盖", `${totals.monitored_enabled_channels ?? totals.enabled_channels}/${totals.enabled_channels}`, "已匹配上游的启用通道"],
    ["24h 调用", totals.calls_24h, `${totals.errors_24h} 次错误`],
    ["24h 计费", fmtUsd(totals.cost_24h_usd), "New API 日消耗"],
    ["7d 计费", fmtUsd(totals.cost_7d_usd), "New API 周消耗"],
    ["累计消耗", fmtUsd(totals.used_usd), "按启用渠道 used_quota 折算"],
    ["当前告警", totals.alerts, "需要处理的当前故障"],
    ["待核对", totals.warnings ?? 0, `${totals.unmatched_enabled_channels ?? 0} 个启用通道未匹配上游`],
'''

HTML_FILTER_OLD = '''            <option value="error">错误多</option>
            <option value="down">无可用通道</option>
'''
HTML_FILTER_NEW = '''            <option value="error">错误多</option>
            <option value="stale">数据待更新</option>
            <option value="inactive">已停用</option>
            <option value="down">无可用通道</option>
'''

CSS_OLD = '''.pill.low_balance,
.pill.slow {
  background: #fff7db;
  color: var(--warn);
}

.pill.error,
.pill.down {
  background: #ffe8ec;
  color: var(--bad);
}
'''
CSS_NEW = '''.pill.low_balance,
.pill.slow,
.pill.stale {
  background: #fff7db;
  color: var(--warn);
}

.pill.error,
.pill.down {
  background: #ffe8ec;
  color: var(--bad);
}

.pill.inactive {
  background: #eef1f4;
  color: #64748b;
}
'''


def replace_once(source: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in source:
        return source, False
    count = source.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one {label} block, found {count}")
    return source.replace(old, new, 1), True


def patch_files(root: Path) -> bool:
    changes = False
    contracts = {
        "app.js": ((APP_HEALTH_OLD, APP_HEALTH_NEW, "health labels"), (APP_SUMMARY_OLD, APP_SUMMARY_NEW, "summary")),
        "index.html": ((HTML_FILTER_OLD, HTML_FILTER_NEW, "health filters"),),
        "styles.css": ((CSS_OLD, CSS_NEW, "health styles"),),
    }
    for relative, replacements in contracts.items():
        target = root / relative
        source = target.read_text(encoding="utf-8")
        updated = source
        changed = False
        for old, new, label in replacements:
            updated, one_changed = replace_once(updated, old, new, label)
            changed = changed or one_changed
        if changed:
            temporary = target.with_name(f".{target.name}.issue28.tmp")
            temporary.write_text(updated, encoding="utf-8")
            os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
            changes = True
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print("patched" if patch_files(args.root) else "already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
