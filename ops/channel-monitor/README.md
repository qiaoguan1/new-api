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
