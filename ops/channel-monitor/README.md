# Channel Monitor Pricing Worker

This directory versions the production automatic-pricing worker and its tests. Runtime credentials,
upstream account metadata, audit output, ledgers, and logs remain under
`/opt/ai-api-stack/channel-monitor/` and are intentionally excluded from Git.

Production schedule:

- `CRON_TZ=Asia/Shanghai` (all monitor job times are Beijing time)
- `0 * * * * generate-monitor-data.py (local dashboard materialization)`
- 08:20: collect the previous complete Beijing day's upstream billing logs.
- 08:30: audit channel availability, price metadata, and actual-cost coverage.
- 08:40: calculate and atomically apply eligible model prices.

Before installing a crontab copied through Windows tooling, normalize it with:

```text
crontab -l > /tmp/root.crontab.current &&
python3 channel-monitor/scripts/sanitize_crontab.py \
  < /tmp/root.crontab.current > /tmp/root.crontab.clean &&
crontab /tmp/root.crontab.clean
```

This removes CRLF/bare carriage returns and an accidental literal `\r` suffix
that can prevent the final cron command from reaching the pricing worker.

The fetch worker supports classic NewAPI billing logs and the newer `/api/v1`
auth/usage API. Every credential gets a dated `complete` or `incomplete` ledger
entry. A zero cost is trusted only after all log pages were fetched successfully.
The pricing worker refuses all database writes while any configured credential
lacks a complete collection for that Beijing business date.

`generate-monitor-data.py` loads `daily_reconciliation.py` to compare the same
Beijing day's upstream account deductions with local `logs.quota`, including
unassigned local calls. The standalone Channel Monitor renders this under
“昨日上游实际扣费与本站计费核对”.

Run tests from the repository root:

```text
python -m unittest discover -s ops/channel-monitor/tests -v
go test ./setting/ratio_setting
```

Run a production preview without database writes:

```text
python3 /opt/ai-api-stack/channel-monitor/scripts/auto-apply-pricing.py --dry-run
```

## Historical overcharge compensation

`historical-overcharge-refund.py` is a guarded one-off compensation tool. It
uses only dated per-model actual-cost ledger evidence, recalculates successful
requests at cost multiplied by 1.5, and emits a checksum-protected dry-run plan.
An incomplete expected upstream blocks the entire modern ledger day.

```text
python3 /opt/ai-api-stack/channel-monitor/scripts/historical-overcharge-refund.py
```

Before live use, review the per-request and per-user plan, run
`--validate-transaction` to execute the complete transaction with a forced
rollback, and retain its plan SHA-256. Live use requires a maintenance window:
stop public nginx ingress, wait at least two configured batch-update intervals,
then pass `--apply --maintenance-confirmed --plan ... --confirm-plan-sha ...`.
The tool creates a mode-0600 balance/source backup, applies user/refund audit
rows atomically, and invalidates only affected quota caches. Reusing the same
frozen plan must report zero new refunds.

Production deployment must first create a root-only rollback directory with
the original generator, generated data, and hashes. Run the patcher once,
compile both Python files, regenerate data, and independently validate the
active-only totals before keeping the change.

## Upstream video catalog normalization

Issue #34 governs mutable upstream Seedance 2.0 names before they reach the
fixed `xtai-relay-v1` catalog. It does not change the desktop application.

Runtime files:

- `config/video-model-policy.json`: versioned stable catalog, reviewed aliases,
  and the explicit publish allowlist. Operators can update this file without a
  NewAPI rebuild, but must increment `revision` and keep a review reason.
- `video_catalog_policy.py`: validates rules, performs fail-closed matching,
  and calculates the strict publish intersection.
- `upstream_video_catalog.py`: loads enabled NewAPI routes in memory, fetches
  `/v1/models` over HTTPS without redirects, sanitizes failures, and preserves
  the last complete per-channel snapshot.
- `scripts/sync-upstream-video-catalog.py`: dry-run/apply entry point.

Validate a rule change without reading the database or contacting upstreams:

```bash
python3 channel-monitor/scripts/sync-upstream-video-catalog.py --validate-only
```

Review current upstream names without writing runtime data:

```bash
python3 channel-monitor/scripts/sync-upstream-video-catalog.py --print-report
```

Atomically refresh the catalog snapshot, mapping report, and candidate
manifests (this still does not edit channels or prices):

```bash
flock -n /run/lock/upstream-video-catalog.lock \
  python3 channel-monitor/scripts/sync-upstream-video-catalog.py --apply-snapshot
```

The fixed catalog is `seedance-2.0`, `seedance-2.0-fast`, and
`seedance-2.0-mini`; resolution is stored separately. A new upstream name is
handled as follows:

1. Exact reviewed source rule.
2. Exact reviewed global rule.
3. Reviewed bounded regex rule.
4. Conservative unique family/variant/resolution parser.
5. Otherwise it remains in `video-model-mapping-report.json` for review.

AI-produced suggestions must be added with `review_state: pending`. They cannot
publish until an operator verifies the upstream meaning, changes the state to
`approved`, records a reason, increments the rule version/policy revision, and
runs validation. `packapi` and `unity2` are intentionally excluded.

Candidate output is gated by approved mapping, current enabled model config,
daily health audit, complete positive actual-cost evidence, and the publish
allowlist. The public candidate file contains only stable model IDs,
resolutions, availability, and protocol/catalog revisions. Upstream names,
channel IDs, costs, credentials, and review notes remain internal.
