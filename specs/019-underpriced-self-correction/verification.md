# Production Verification

## Test and Review Gates

- Focused red/green tests reproduced and fixed the permanent safety block.
- Full channel-monitor suite: 86/86 passed.
- Python compilation passed for the pricing worker, actual-cost audit policy,
  scanner patcher, and patched production scanner copy.
- The patcher transformed the exact production scanner and passed an
  idempotent `--check` run.
- Two comprehensive-review artifacts were posted to Issue #19; the final
  artifact reports two fixed findings, zero deferred, and zero unaddressed.

## Production Backup and Deployment

- Full rollback bundle:
  `/opt/ai-api-stack/backups/issue19-underpriced-recovery-20260724-024006`
- Bundle permissions: `0700`; SHA-256 manifest verified before deployment.
- Pricing transaction rollback:
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-07-23-20260724T024836+0800.json`
- Deployed the pricing worker and actual-cost audit policy, then patched and
  syntax-checked the production daily scanner.

## Dated Replay and Live Execution

- Business day: `2026-07-23` (Beijing day override).
- Daily audit: 11 enabled channels, 11 healthy, 0 failed, 0 critical alerts.
- Remaining warnings: one `pricing_scan_failed` and one
  `actual_cost_unavailable`; neither is model-scoped or critical.
- `gpt-5.6-sol` model-scoped alerts: 0 (previous catalog-price false positives
  were removed).
- Dry run and live run: 809 discovered, 8 safely applicable, 801 unchanged.
- `gpt-5.6-sol` trusted eligible worst actual costs:
  input `1.03` and output `6.18`, both from `maolao`.
- Final settings: `ModelRatio=5.15`, `CompletionRatio=6.0`, no fixed
  `ModelPrice`; all customer groups remain `0.15`.
- Final customer price: input `1.545`, output `9.27`, exactly actual cost
  multiplied by `1.5`. The settings were already correct, so the guarded live
  transaction made no net price change.

## Monitoring and Schedule

- Monitor materialization regenerated successfully.
- NewAPI, PostgreSQL, Redis, and the remaining production stack are running;
  health-enabled core containers report healthy.
- `CRON_TZ=Asia/Shanghai` is present.
- Monitor materialization is hourly (`0 * * * *`).
- Daily fetch/audit/pricing remain Beijing 08:20 / 08:30 / 08:40.
- Public performance data contains exactly model name, latency, success rate,
  output speed, and request count. Anonymous internal-channel access returns
  401 and exposes no channel name, upstream, margin, cost, or internal error.

## Ten-Round Result

All 10 rounds passed the same independent checks:

- 11/11 configured upstream collections complete;
- critical alerts 0 and `gpt-5.6-sol` alerts 0;
- actual-cost and 1.5x sell-price formula exact;
- database option maps equal the live transaction backup (no unintended
  changes to the other models);
- deployed script hashes and scanner policy correct;
- cron/timezone correct;
- core container health correct;
- status endpoint 200, internal monitor anonymous access 401, public model
  metrics 200 with only the allowed five fields.

## 08:40 Cron Follow-up

- On 2026-07-24 the 08:20 collection and 08:30 audit ran, but the 08:40 cron
  entry ended in the two literal characters `\r`, so its shell redirection did
  not reach the pricing worker.
- Backups were created before both diagnostic passes:
  `/opt/ai-api-stack/backups/issue19-crontab-crlf-20260724-150943` and
  `/opt/ai-api-stack/backups/issue19-crontab-literal-cr-20260724-151317`.
- A tracked, tested crontab sanitizer now handles CRLF, bare CR, and a literal
  trailing `\r`, rejects NUL/empty inputs, and writes exactly one terminal LF.
- The installed crontab contains neither a CR byte nor a literal `\r` suffix;
  the 08:40 line ends exactly at `2>&1`.
- The command extracted from the installed cron entry was executed unchanged at
  15:13 Beijing time. It produced a new successful live run for 2026-07-23:
  8 applicable models, 801 unchanged, zero errors, and a new pricing backup at
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-07-23-20260724T151318+0800.json`.
- Full channel-monitor suite after this follow-up: 90/90 passed.
- Ten follow-up production rounds all confirmed: clean cron bytes, one exact
  08:40 entry, latest live result 8 applied / 801 unchanged, zero critical
  alerts, deployed sanitizer hash match, and healthy NewAPI.
