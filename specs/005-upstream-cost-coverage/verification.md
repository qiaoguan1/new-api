# Verification: Upstream Credential and Cost Coverage Repair

**Production day**: 2026-07-22  
**Executed**: 2026-07-23 (Asia/Shanghai)

**Pull request**: https://github.com/qiaoguan1/new-api/pull/6

## Credentials

- Imported 11 recognized account groups and preserved the existing codeplan entry: 12 total.
- Credential file: `0600 root:root`.
- Credential rollback: `/root/maintenance-backups/20260723-175905-issue5-upstream-creds`.
- Raw upload was removed after atomic normalization.
- Historical `/var/log/upstream-balance.log` contained seven username occurrences. They were
  replaced with `[REDACTED]`; the post-cleanup secret scan reported zero remaining occurrences.

## 08:20 Upstream Fetch

- Result: 7 ok, 5 error, 0 skipped.
- Real billing rows were collected for codeplan, jojocode, maolao, and nodyhub.
- 0809, icreat, and runapi logged in successfully but had no rows for the target day.
- aihua uses a non-NewAPI login flow and returned 404 at the standard endpoint.
- apikeyfun, token-bridge, and unity2 expose a different `/api/v1/auth/*` frontend contract; direct
  credential requests were intercepted by the site frontend and returned HTML instead of an API
  token response.
- packapi requires a Tencent captcha ticket. The collector did not bypass it.
- Failures were isolated; no username or password was emitted by the patched worker.

## 08:30 Audit

- 10 enabled channels: 8 ok and 2 failed.
- All three image channels selected `gpt-image-2` and returned HTTP 200:
  maolao image, jojocode image, and codeplan image.
- The old `gpt-5.5` image-channel probe failure is resolved.
- Enabled channel configuration contains 797 unique model names. The upstream pricing scan contains
  728 unique matching price entries; the upstream catalog does not add names to the inventory.
- Banana image failed on its selected configured image model because the upstream default group had
  no available route. Paisio failed because its first configured model is unsupported by the group.
  These are retained as channel-level blockers rather than converted into guessed prices.

## 08:40 Pricing

- A review found that a model-specific critical alert incorrectly blocked the entire channel. The
  gate now blocks only that model; a channel-level alert still blocks the whole channel.
- Final dry-run: 798 discovered, 6 applicable, 792 retained.
  - 722: `no_trusted_actual_cost`
  - 68: `no_healthy_enabled_channel`
  - 2: `critical_model_alert`
- Live transaction output: `BEGIN`, `DO`, `COMMIT`.
- Pricing rollback:
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-07-22-20260723T101653Z.json`
  (`0600 root:root`).

| Model | Actual cost | Applied base value | Group | Customer price | Check |
|---|---:|---:|---:|---:|---:|
| gpt-4o-mini input/output | 0.15 / 0.60 CNY/M | ModelRatio 0.75, CompletionRatio 4 | 0.15 | 0.225 / 0.90 | 1.5x |
| gpt-5.5 input/output | 5.00 / 40.00 CNY/M | ModelRatio 25, CompletionRatio 8 | 0.15 | 7.50 / 60.00 | 1.5x |
| gpt-image-2 | 0.103 CNY/call | ModelPrice 1.03 | 0.15 | 0.1545 | 1.5x |
| grok-video-3 | 1.00 CNY/call | ModelPrice 10 | 0.15 | 1.50 | 1.5x |
| omni-flash | 2.80 CNY/call | ModelPrice 28 | 0.15 | 4.20 | 1.5x |
| wan2.6-t2v | 5.00 CNY/call | ModelPrice 50 | 0.15 | 7.50 | 1.5x |

## Runtime and Schedule

- NewAPI container: running and healthy; image unchanged.
- Cron remains active at 08:20 fetch, 08:30 audit, and 08:40 pricing.
- NewAPI reloads options from the database every 60 seconds. After one sync interval, public
  `/api/pricing` matched the database for gpt-5.5, gpt-image-2, grok-video-3, omni-flash, and
  wan2.6-t2v. `gpt-4o-mini` is stored correctly but is not exposed in the unauthenticated pricing
  list for the currently usable public groups.
- Production script rollback:
  `/root/maintenance-backups/20260723-180705-issue5-audit-pricing`.

## Automated Checks

- Python unit tests: 18 passed.
- Python syntax compilation: passed for all changed runtime and patch scripts.
- Production patch preflight: 12/12 scan replacements and 1/1 fetch replacement matched.
- `git diff --check`: passed.
- Repository credential scan: no supplied username, password, API key, or server credential found.

## 2026-07-23 Full Reconciliation Follow-up

### Deployment and rollback

- Production rollback backup:
  `/root/maintenance-backups/20260723-104606-issue5-full-reconciliation`
- Paisio credential rollback backup:
  `/root/maintenance-backups/20260723-105453-paisio-credential`
- Credential file remained `0600 root:root`; credentials and bearer tokens were not printed,
  persisted in Git, or added to monitor JSON.

### Previous UTC day collection (`2026-07-22`)

- Complete credential collections: 11 of 13.
- Classic NewAPI complete: 0809, codeplan, icreat, jojocode, maolao, nodyhub, paisio,
  and runapi.
- `/api/v1` usage complete: aihua, apikeyfun, and token-bridge.
- Trusted complete-zero results: 0809, aihua, apikeyfun, icreat, paisio, runapi, and
  token-bridge.
- Incomplete (null cost, never zero): packapi requires Tencent CAPTCHA; unity2 requires
  Turnstile.
- Complete upstream deduction sum: `47.762002` CNY.

### Same-day local reconciliation

- Direct UTC-date database total: `78.270516` CNY, 327 log rows/calls.
- Monitor total: `78.270516` CNY, 327 calls.
- Mapped calls: 323; explicitly disclosed unassigned `channel_id=0` error logs: 4 calls,
  `0.000000` CNY.
- Monitor inventory: 16 upstream definitions, 11 complete, 5 incomplete, 3 without account
  credentials. Overall difference and margin are null while reconciliation is incomplete.
- Notable complete comparisons: codeplan `0.526287` upstream vs `0.154500` local;
  jojocode `8.146904` vs `31.085576`; maolao `3.735917` vs `23.854776`; nodyhub
  `35.352894` vs `23.175664`; paisio `0` vs `0` with 49 local failed calls.

### Pricing safety and UI

- Dry-run and live 08:40 executions both failed closed with
  `upstream collection incomplete: packapi, unity2`.
- Both pricing log records have no `database_output` and no backup path, confirming no option
  transaction started.
- Group ratio remains uniformly `0.15`; `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`,
  `gpt-5.5`, `gpt-image-2`, and the previously applied fixed-price models retained their values.
- 30 focused unit tests pass. Python compile checks, generated JavaScript syntax check, and
  browser-rendered reconciliation checks pass.
- The production URL is protected by HTTP Basic Auth and neither available browser session had
  valid credentials. The exact production static assets and monitor JSON were therefore rendered
  through a local read-only fixture for DOM verification; the production files are mounted directly
  by Nginx and require no rebuild or restart.

## 2026-07-23 Retired-Upstream and Pricing-Signal Follow-up

### Retirement and recovery

- Removed `packapi` and `unity2` from the root-owned credential file, upstream definitions, and
  every dated ledger entry. Credentials now contain 11 retained accounts and definitions contain
  14 visible upstreams.
- Preserved database channels 15, 21, 22, and 29 for history; all four remain disabled (`status=2`).
- Recovery backup: `/root/maintenance-backups/20260723-190947-retire-packapi-unity2`.
  The credential copy is `0600 root:root`.

### Replay and reconciliation (`2026-07-22`)

- Collection replay: 11 complete, 0 incomplete. Actual upstream deduction: `47.762002` CNY.
- Audit replay: 10 enabled channels, 8 available, 2 failed. Availability failures remain blockers.
- Reconciliation: required 11/11 complete, optional 3, overall complete.
- Local NewAPI billing: `78.270516` CNY across 327 calls; 323 mapped plus four unassigned
  zero-cost errors. Difference: `30.508514` CNY; gross margin: `0.3898`.
- Any future unassigned log with positive billed quota now makes reconciliation incomplete.

### Corrected price transaction

- Manual review found that `low_margin_risk` and `price_below_upstream_*` alerts were excluding the
  actual-cost evidence needed to correct those risks. Those named pricing signals no longer block;
  availability, billing-integrity, global, and unknown critical alerts still fail closed.
- Corrected dry-run: 798 discovered, 7 applicable, 791 retained.
- Corrected live transaction: `BEGIN`, `DO`, `COMMIT`.
- Pricing rollback:
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-07-22-20260723T112107Z.json`.

| Model | Highest trusted actual cost | Applied base value | Customer price at 0.15 | Check |
|---|---:|---:|---:|---:|
| gpt-4o-mini input/output | 0.15 / 0.60 CNY/M | 1.50 / 6.00 CNY/M | 0.225 / 0.90 CNY/M | 1.5x |
| gpt-5.5 input/output | 5.00 / 40.00 CNY/M | 50.00 / 400.00 CNY/M | 7.50 / 60.00 CNY/M | 1.5x |
| gpt-5.6-sol input/output | 1.03 / 6.18 CNY/M | 10.30 / 61.80 CNY/M | 1.545 / 9.27 CNY/M | 1.5x |
| gpt-image-2 | 0.525757 CNY/call | ModelPrice 5.25757 | 0.7886355 CNY/call | 1.5x |
| grok-video-3 | 1.00 CNY/call | ModelPrice 10 | 1.50 CNY/call | 1.5x |
| omni-flash | 2.80 CNY/call | ModelPrice 28 | 4.20 CNY/call | 1.5x |
| wan2.6-t2v | 5.00 CNY/call | ModelPrice 50 | 7.50 CNY/call | 1.5x |

### Final checks

- 38 focused Python tests pass; Python compilation, deployed JavaScript syntax, and
  `git diff --check` pass.
- Production DB values, uniform group ratio `0.15`, ledger completeness, monitor arithmetic,
  retired-slug absence, and disabled historical channel rows were independently re-read and asserted.
- The local workstation did not have a Go executable, and this follow-up changed no Go files.
