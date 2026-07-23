#!/usr/bin/env python3
"""Add explicit NewAPI and upstream-collection entries to the standalone monitor."""

import argparse
import os
from pathlib import Path


TOOLBAR_OLD = """          <button id="manageBtn" type="button" class="secondary-btn">管理渠道</button>
          <a href="./upstreams-admin.html" class="secondary-btn" style="text-decoration:none;display:inline-flex;align-items:center;">上游凭证</a>"""

TOOLBAR_NEW = """          <a href="/channels?action=create" class="secondary-btn" style="text-decoration:none;display:inline-flex;align-items:center;">＋ 添加渠道</a>
          <a href="/channels" class="secondary-btn" style="text-decoration:none;display:inline-flex;align-items:center;">NewAPI 渠道</a>
          <button id="manageBtn" type="button" class="secondary-btn">监控配置</button>
          <a href="./upstreams-admin.html" class="secondary-btn" style="text-decoration:none;display:inline-flex;align-items:center;">上游采集配置</a>"""

SUMMARY_OLD = """      </header>

      <section class="summary" id="summary"></section>"""

SUMMARY_NEW = """      </header>

      <section class="meta channel-setup-guide">
        <span><strong>新增渠道需要两步：</strong>先添加 NewAPI 渠道，再填写上游账单账号与充值换算率；两步完成后才会纳入每日实际扣费对账与自动改价。</span>
      </section>

      <section class="summary" id="summary"></section>"""


def _replace_once_or_verify(source, old, new, label):
    old_count = source.count(old)
    new_count = source.count(new)
    if old_count == 1 and new_count == 0:
        return source.replace(old, new, 1)
    if old_count == 0 and new_count == 1:
        return source
    raise RuntimeError(
        f"expected one legacy or patched {label}, found old={old_count}, new={new_count}"
    )


def transform(source):
    source = _replace_once_or_verify(
        source, TOOLBAR_OLD, TOOLBAR_NEW, "channel toolbar"
    )
    return _replace_once_or_verify(
        source, SUMMARY_OLD, SUMMARY_NEW, "channel setup guide"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.target.read_text(encoding="utf-8")
    updated = transform(source)
    if args.check and updated != source:
        raise RuntimeError("target still requires the channel entry patch")
    if not args.check and updated != source:
        temporary = args.target.with_name(args.target.name + ".channel-entry.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, args.target.stat().st_mode & 0o777)
        os.replace(temporary, args.target)
    print(
        "channel management entries verified: "
        f"mode={'check' if args.check else 'apply'}, changed={updated != source}"
    )


if __name__ == "__main__":
    main()
