#!/usr/bin/env python3
"""Idempotently add protected video-provider reconciliation to the monitor UI."""

from __future__ import annotations

import pathlib
import sys


HTML_MARKER = "video-consumption-reconciliation-v1"
HTML_ANCHOR = '      <section class="panel pricing-panel">'
HTML_BLOCK = '''      <!-- video-consumption-reconciliation-v1 -->
      <section class="panel video-consumption-panel">
        <div class="section-head">
          <div>
            <p class="eyebrow">Video Upstream Reconciliation</p>
            <h2>视频中转任务与上游实际消耗</h2>
          </div>
          <span id="videoConsumptionDate">正在读取视频对账...</span>
        </div>
        <div class="table-wrap compact">
          <table>
            <thead><tr>
              <th>视频上游</th><th>采集状态</th><th>中转任务</th><th>成功率</th>
              <th>上游任务证据</th><th>实扣覆盖率</th><th>上游实扣</th><th>中转报价</th><th>最后抓取</th>
            </tr></thead>
            <tbody id="videoConsumptionRows"><tr><td colspan="9">正在读取...</td></tr></tbody>
          </table>
        </div>
      </section>

'''

JS_MARKER = "// video-consumption-reconciliation-v1"
JS_ANCHOR = "function renderDynamicPricing(pricing) {"
JS_BLOCK = '''// video-consumption-reconciliation-v1
function renderVideoConsumption(snapshot) {
  const tbody = document.querySelector("#videoConsumptionRows");
  const dateBox = document.querySelector("#videoConsumptionDate");
  if (!tbody || !dateBox) return;
  if (!snapshot || !Array.isArray(snapshot.providers)) {
    dateBox.textContent = "暂无视频对账数据";
    tbody.innerHTML = '<tr><td colspan="9">暂无视频对账数据。</td></tr>';
    return;
  }
  dateBox.textContent = `北京时间业务日：${snapshot.date || "-"}`;
  tbody.innerHTML = snapshot.providers.map((row) => {
    const status = row.collection_status || "incomplete";
    const successRate = row.success_rate === null || row.success_rate === undefined
      ? "-" : `${(Number(row.success_rate) * 100).toFixed(2)}%`;
    const coverage = row.actual_cost_coverage === null || row.actual_cost_coverage === undefined
      ? "-" : `${(Number(row.actual_cost_coverage) * 100).toFixed(2)}%`;
    const actual = row.actual_log_complete ? fmtCny(row.upstream_actual_cost_cny, 6) : "未知";
    return `<tr>
      <td><span class="primary">${escapeHtml(row.provider_id || "-")}</span></td>
      <td><span class="recon-status ${escapeHtml(status)}">${collectionStatusText[status] || escapeHtml(status)}</span></td>
      <td class="number">${fmtInt(row.task_count)}</td>
      <td class="number">${successRate}</td>
      <td class="number">${fmtInt(row.provider_evidence_count)}</td>
      <td class="number">${coverage}</td>
      <td class="number"><strong>${actual}</strong></td>
      <td class="number">${fmtCny(row.relay_sale_cny, 6)}</td>
      <td>${row.last_fetch_at ? fmtTime(row.last_fetch_at) : "-"}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="9">暂无视频任务。</td></tr>';
}

'''


def patch_html(source: str) -> str:
    """Insert the protected video reconciliation table once."""
    if HTML_MARKER in source:
        return source
    if HTML_ANCHOR not in source:
        raise RuntimeError("index.html pricing panel anchor was not found")
    return source.replace(HTML_ANCHOR, HTML_BLOCK + HTML_ANCHOR, 1)


def patch_js(source: str) -> str:
    """Insert the renderer and call it from the existing render function once."""
    if JS_MARKER in source:
        return source
    if JS_ANCHOR not in source:
        raise RuntimeError("app.js function anchor was not found")
    call_anchor = "  renderReconciliation(state.data.daily_business);"
    if call_anchor not in source:
        raise RuntimeError("app.js render call anchor was not found")
    patched = source.replace(JS_ANCHOR, JS_BLOCK + JS_ANCHOR, 1)
    return patched.replace(
        call_anchor,
        call_anchor + "\n  renderVideoConsumption(state.data.video_consumption);",
        1,
    )


def _replace(path: pathlib.Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(
        "/opt/ai-api-stack/channel-monitor"
    )
    html_path = root / "index.html"
    js_path = root / "app.js"
    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")
    patched_html = patch_html(html)
    patched_js = patch_js(js)
    if patched_html != html:
        _replace(html_path, patched_html)
    if patched_js != js:
        _replace(js_path, patched_js)
    print("video consumption UI patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
