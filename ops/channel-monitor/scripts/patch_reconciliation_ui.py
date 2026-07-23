#!/usr/bin/env python3
"""Add the previous-day reconciliation panel to the standalone monitor UI."""

import argparse
import os
from pathlib import Path


INDEX_PANEL = r'''
      <section class="panel reconciliation-panel">
        <div class="section-head">
          <div>
            <p class="eyebrow">Daily Reconciliation</p>
            <h2>昨日上游实际扣费与本站计费核对</h2>
          </div>
          <span id="reconciliationDate">正在读取对账日期...</span>
        </div>
        <div id="reconciliationSummary" class="reconciliation-summary"></div>
        <div class="table-wrap compact">
          <table class="reconciliation-table">
            <thead>
              <tr>
                <th>上游</th>
                <th>采集状态</th>
                <th>渠道</th>
                <th>上游实际扣费</th>
                <th>本站计费金额</th>
                <th>差额</th>
                <th>毛利率</th>
                <th>调用 / 日志</th>
                <th>数据源 / 错误</th>
              </tr>
            </thead>
            <tbody id="reconciliationRows">
              <tr><td colspan="9">正在读取...</td></tr>
            </tbody>
          </table>
        </div>
      </section>

'''


APP_FUNCTION = r'''
const collectionStatusText = {
  complete: "已完整获取",
  incomplete: "获取失败",
  missing: "今日未采集",
  no_credentials: "缺少账户凭证",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderReconciliation(business) {
  const reconciliation = business?.reconciliation;
  const tbody = document.querySelector("#reconciliationRows");
  const summaryBox = document.querySelector("#reconciliationSummary");
  const dateBox = document.querySelector("#reconciliationDate");
  if (!tbody || !summaryBox || !dateBox) return;
  if (!reconciliation) {
    dateBox.textContent = "暂无对账数据";
    summaryBox.innerHTML = '<span class="recon-alert">尚未生成同日对账结果</span>';
    tbody.innerHTML = '<tr><td colspan="9">暂无对账数据。</td></tr>';
    return;
  }
  const totals = reconciliation.totals || {};
  const complete = reconciliation.complete === true;
  dateBox.textContent = `核对北京时间业务日：${reconciliation.date || business?.date || "-"}`;
  summaryBox.innerHTML = `
    <span class="${complete ? "recon-ok" : "recon-alert"}">${complete ? "全量对账完成" : "对账未完成，自动改价已安全阻断"}</span>
    <span>完整 ${fmtInt(totals.complete_upstreams)} / ${fmtInt(totals.expected_upstreams)} 家</span>
    <span>未完成 ${fmtInt(totals.incomplete_upstreams)} 家</span>
    <span>缺凭证 ${fmtInt(totals.credentialless_upstreams)} 家</span>
    <span>本站 ${fmtCny(totals.local_billed_cny, 6)}</span>
    <span>本站调用 ${fmtInt(totals.local_calls)}（未归属 ${fmtInt(totals.unassigned_local_calls)}）</span>
    <span>已获取上游 ${fmtCny(totals.upstream_actual_cost_cny, 6)}</span>
    <span>全量差额 ${complete ? fmtCny(totals.difference_cny, 6) : "不可计算"}</span>
  `;
  const rows = reconciliation.rows || [];
  tbody.innerHTML = rows.map((row) => {
    const status = row.collection_status || "incomplete";
    const actual = row.actual_log_complete ? fmtCny(row.upstream_actual_cost_cny, 6) : "未获取";
    const difference = row.actual_log_complete ? fmtCny(row.difference_cny, 6) : "不可计算";
    const margin = row.gross_margin === null || row.gross_margin === undefined
      ? "-"
      : `${(Number(row.gross_margin) * 100).toFixed(2)}%`;
    const retryWarning = row.actual_log_complete && row.last_attempt_status === "incomplete"
      ? '<span class="secondary recon-alert">最近重试失败，保留此前完整结果</span>'
      : "";
    const source = row.actual_log_complete
      ? `${escapeHtml(row.billing_api || "billing_log")} / NewAPI logs`
      : escapeHtml(row.collection_error || "未提供可用的实际扣费接口或凭证");
    return `
      <tr class="${row.actual_log_complete ? "" : "recon-incomplete-row"}">
        <td><span class="primary">${escapeHtml(row.name || row.slug)}</span><span class="secondary">${escapeHtml(row.slug)}</span></td>
        <td><span class="recon-status ${escapeHtml(status)}">${collectionStatusText[status] || escapeHtml(status)}</span>${retryWarning}</td>
        <td><span class="primary">${fmtInt(row.enabled_channels)} / ${fmtInt(row.channel_count)} 启用</span></td>
        <td class="number"><strong>${actual}</strong></td>
        <td class="number">${fmtCny(row.local_billed_cny, 6)}</td>
        <td class="number">${difference}</td>
        <td class="number">${margin}</td>
        <td><span class="primary">本站 ${fmtInt(row.local_calls)}</span><span class="secondary">上游 ${row.upstream_log_rows ?? "-"}</span></td>
        <td><span class="secondary">${source}</span></td>
      </tr>`;
  }).join("") || '<tr><td colspan="9">暂无上游记录。</td></tr>';
}

'''


STYLES = r'''

.reconciliation-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem 1.25rem;
  margin: 0 0 1rem;
  padding: 0.9rem 1rem;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.03);
  font-size: 0.88rem;
}

.recon-ok { color: #047857; font-weight: 700; }
.recon-alert { color: #b91c1c; font-weight: 700; }
.recon-incomplete-row { background: rgba(239, 68, 68, 0.055); }
.recon-status {
  display: inline-flex;
  width: max-content;
  padding: 0.22rem 0.55rem;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 700;
}
.recon-status.complete { color: #047857; background: #d1fae5; }
.recon-status.incomplete,
.recon-status.missing,
.recon-status.no_credentials { color: #b91c1c; background: #fee2e2; }
.reconciliation-table td { vertical-align: top; }
'''


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def write_atomic(path, content):
    temporary = path.with_name(path.name + ".reconciliation.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def transform(root):
    index_path = root / "index.html"
    app_path = root / "app.js"
    styles_path = root / "styles.css"
    index = replace_once(
        index_path.read_text(encoding="utf-8"),
        '      <section class="panel pricing-panel">\n',
        INDEX_PANEL + '      <section class="panel pricing-panel">\n',
        "index panel",
    )
    app = replace_once(
        app_path.read_text(encoding="utf-8"),
        "function renderDynamicPricing(pricing) {\n",
        APP_FUNCTION + "function renderDynamicPricing(pricing) {\n",
        "render function",
    )
    app = replace_once(
        app,
        "  renderSummary(state.data.totals);\n",
        "  renderSummary(state.data.totals);\n  renderReconciliation(state.data.daily_business);\n",
        "render call",
    )
    styles = styles_path.read_text(encoding="utf-8") + STYLES
    return index, app, styles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    index, app, styles = transform(args.root)
    if not args.check:
        write_atomic(args.root / "index.html", index)
        write_atomic(args.root / "app.js", app)
        write_atomic(args.root / "styles.css", styles)
    print(f"reconciliation UI patch verified: write={not args.check}")


if __name__ == "__main__":
    main()
