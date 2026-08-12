# Channel Monitor Pricing Worker

This directory versions the production automatic-pricing worker and its tests. Runtime credentials,
upstream account metadata, audit output, ledgers, and logs remain under
`/opt/ai-api-stack/channel-monitor/` and are intentionally excluded from Git.

Production schedule:

- `CRON_TZ=Asia/Shanghai` (all monitor job times are Beijing time)
- `0 * * * * generate-monitor-data.py (local dashboard materialization)`
- 08:20: collect the previous complete Beijing day's upstream billing logs.
- 08:30: audit channel availability, price metadata, and actual-cost coverage.
- 08:35: apply video prices from the reviewed official catalog at exactly 1.5x.
- 08:40: calculate and atomically apply eligible model prices.
- 09:12-12:12: retry the prior-day operations digest until one delivery succeeds.

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
The generic pricing worker isolates incomplete collection by expected channel and
model. An incomplete account preserves only the models that depend on that
account; unrelated text/image models with trusted evidence continue in the same
atomic run. A shared model remains protected if any expected enabled source is
incomplete.

Video pricing is intentionally separate. `apply-official-video-pricing.py`
uses only the versioned official catalog and reviewed raw-model mappings for
downstream charging. Generic automatic pricing always skips recognized video
models. Upstream video deductions and authenticated catalog prices are retained
only as exact-route internal cost/profit evidence; they never set a sale price.

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
daily health audit, official-price coverage, and the publish allowlist. Missing
upstream cost evidence does not block an officially priced healthy route. The
public candidate file contains only stable model IDs, resolutions,
availability, downstream sale prices, and protocol/catalog/pricing revisions.
Upstream names, channel IDs, provider costs, markup, margins, profits,
credentials, and review notes remain internal.

## Video consumption reconciliation

Issue #43 reconciles the durable video gateway with authenticated Toonflow and
Paisio billing evidence. Run `fetch-upstream-balance.py` first, then run
`generate-video-consumption-monitor.py`. The latter reads the gateway SQLite
database in read-only mode and atomically writes:

- `data/video-consumption-private.json`: provider/task reconciliation for the
  Basic-Auth-protected channel monitor.
- `data/video-model-health-public.json`: stable model health only; no provider,
  channel, cost, price, margin, credential, or upstream fields.

Paisio task IDs come from its authenticated `/api/task/self` endpoint and are
accepted as cost evidence only when their total agrees with the complete
billing log. Toonflow uses its authenticated web operation log. Because its
login requires CAPTCHA, unattended cron never logs in or solves a CAPTCHA. An
operator-authorized Bearer token is stored only in the root-readable file
`secrets/toonflow-web-token` (override with
`CHANNEL_MONITOR_TOONFLOW_TOKEN_FILE`). Missing or expired tokens produce an
incomplete/null result rather than a fabricated zero.

The protected monitor generator is patched with
`patch_generate_video_consumption.py`; the protected UI is patched with
`patch_video_consumption_ui.py`. Both patchers are idempotent and fail when the
reviewed production anchors change. Video provider cost remains comparison
evidence only. Downstream video quotes continue to use Ark official pricing
multiplied by 1.5.

## Video settlement publisher

After `generate-video-consumption-monitor.py` completes, run
`scripts/publish-video-settlements.py`. It publishes only completed, exact
provider-task matches. Missing, ambiguous, running, stale, or inferred records
remain pending and never become a fabricated charge.

The gateway uses `VIDEO_SETTLEMENT_GATEWAY_URL` and the root-readable
`VIDEO_SETTLEMENT_TOKEN_FILE`. Ordinary NewAPI users are enabled by setting
`NEWAPI_SETTLEMENT_URL`, `NEWAPI_SETTLEMENT_USER_ID`, and the root-readable
`NEWAPI_SETTLEMENT_ACCESS_TOKEN_FILE`. The NewAPI account must be root. Run the
publisher after the daily provider ledger is complete; retries are safe because
settlement IDs and evidence fingerprints are deterministic.

```text
fetch-upstream-balance.py
generate-video-consumption-monitor.py
publish-video-settlements.py
```

The hourly monitor may refresh health data, while final settlement should
follow the complete daily ledger and may be retried safely.

## Upstream balance email alerts

`scripts/monitor-upstream-balances.py` performs a lightweight authenticated
balance probe for every enabled upstream that has credentials. It does not
fetch billing logs, price catalogs, task history, or call a paid model. Its
live snapshot and alert state are separate from the daily actual-cost ledger:

- `data/upstream-balance-live.json`
- `data/upstream-balance-alert-state.json`

A finite raw provider balance at or below
`UPSTREAM_BALANCE_ALERT_THRESHOLD` (default `0`) is depleted. Missing balance
fields, login failures, timeouts, malformed responses, and expired Toonflow
authorization are `unknown`, never zero. The third consecutive unknown result
creates a separate monitor-failure notification. Depletion and monitor-failure
events are deduplicated across restarts; depletion reminders default to 24
hours, and recovery is sent once after a previously delivered alert.

The monitor does not store SMTP credentials. It calls the RootAuth-protected
`POST /api/option/upstream_balance_alert` endpoint using the existing NewAPI
root access-token file. NewAPI constructs the subject and HTML body internally,
then uses its configured SMTP transport. The endpoint sends only to
`UPSTREAM_BALANCE_ALERT_EMAIL`; the caller cannot choose a recipient, subject,
SMTP setting, or arbitrary HTML.

Copy `balance-alert.env.example` to the untracked, mode-0600
`balance-alert.env`, set the internal NewAPI URL/root user ID/token-file path,
and set `UPSTREAM_BALANCE_ALERT_EMAIL` in the NewAPI container environment.
Validate without sending or changing alert state:

```bash
set -a
. /opt/ai-api-stack/channel-monitor/balance-alert.env
set +a
python3 /opt/ai-api-stack/channel-monitor/scripts/monitor-upstream-balances.py --dry-run
```

Send one explicit transport test after deployment:

```bash
set -a
. /opt/ai-api-stack/channel-monitor/balance-alert.env
set +a
python3 /opt/ai-api-stack/channel-monitor/scripts/monitor-upstream-balances.py --test-email
```

Run once per hour in Beijing time under a non-overlapping lock:

```cron
CRON_TZ=Asia/Shanghai
2 * * * * cd /opt/ai-api-stack && /usr/bin/flock -n /run/lock/upstream-balance-alert.lock /bin/bash -c 'set -a; . channel-monitor/balance-alert.env; set +a; exec /usr/bin/python3 channel-monitor/scripts/monitor-upstream-balances.py' >> /var/log/upstream-balance-alert.log 2>&1
```

## Daily operations digest

`scripts/daily-ops-digest.py` combines the prior Beijing business day's private
ledger, audit, automatic-pricing run and the latest live balance snapshot. The
fixed-recipient email includes every enabled channel's collection/audit/balance
state, prior-day and month-to-date call/cost totals, plus pricing apply/skip/block
counts. Unknown collection stays unknown and is never rendered as zero.

The script sends bounded structured JSON to the RootAuth-protected
`POST /api/option/upstream_ops_digest` endpoint. NewAPI validates the structure,
caps the body at 64 KiB, HTML-escapes all names and uses the same fixed
`UPSTREAM_BALANCE_ALERT_EMAIL` recipient and configured SMTP transport. Delivery
state is written only after success, so the retry window is idempotent:

```cron
CRON_TZ=Asia/Shanghai
12 9-12 * * * cd /opt/ai-api-stack && /usr/bin/flock -n /run/lock/upstream-daily-ops-digest.lock /bin/bash -c 'set -a; . channel-monitor/balance-alert.env; set +a; exec /usr/bin/python3 channel-monitor/scripts/daily-ops-digest.py' >> /var/log/upstream-daily-ops-digest.log 2>&1
```

Preview without sending or changing delivery state:

```bash
python3 /opt/ai-api-stack/channel-monitor/scripts/daily-ops-digest.py --dry-run
```
