# Verification

## Local and Staging

- 75 Python channel-monitor tests passed.
- TypeScript typecheck, targeted Prettier, targeted ESLint, and the production
  frontend build passed.
- A full Docker build compiled both the frontend and Go application into
  `new-api-fixed:beijing-time-20260724-issue17`.
- Exact copies of the production audit worker, monitor generator, and internal
  monitor page were patched in `/tmp` first. Python compilation and a second
  idempotency run passed; worker modes remained 0755 and `app.js` remained 0644.
- The Beijing boundary fixture maps `2026-07-23T16:30:00Z` to
  `2026-07-24T00:30:00+08:00` and resolves the previous complete business day
  as `2026-07-23`.

## Production Deployment

- Rollback backup verified at
  `/opt/ai-api-stack/backups/issue17-beijing-time-20260724-0107`.
- Server timezone is `Asia/Shanghai`; root crontab now explicitly declares
  `CRON_TZ=Asia/Shanghai` before the hourly and daily monitor jobs.
- The monitor materialization schedule remains hourly. Collection, audit, and
  pricing remain 08:20, 08:30, and 08:40 Beijing time.
- Active image is `new-api-fixed:beijing-time-20260724-issue17`; the NewAPI
  container is running and healthy.

## Beijing Business-Day Replay

- Replayed business day: `2026-07-23`.
- All 11 required upstream accounts completed collection; zero required
  accounts were incomplete. The ledger contains 375 upstream billing rows and
  CNY 30.89108973 actual upstream cost.
- Channel audit reports 11 enabled, 11 healthy, and zero failed channels.
- Local NewAPI aggregation uses the Beijing partition: 332 rows and quota
  147499080, versus 327 rows under the former UTC partition.
- Local billed amount is CNY 294.99816. Of 332 calls, 326 are mapped. The six
  unmapped calls (CNY 5.625) are all channel 43, Topaz video upscaling. That
  channel has no trusted actual upstream cost, so all its models were excluded
  from pricing.

## Pricing

- Dry-run discovered 809 models. Seven models had trusted actual cost and
  passed every gate; 802 retained their existing price.
- The live transaction returned `COMMIT` and created a mode-0600 pricing backup
  at
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-07-23-20260724T011553+0800.json`.
- Updated models: `gpt-4o-mini`, `gpt-5.5`, `gpt-image-2`, `sd2-720p`,
  `sd2-fast-480p`, `video-fast-480p`, and `video-fast-720p`.
- All frontend group ratios remain 0.15 and the pricing markup remains 1.5.
- `gpt-5.6-sol` retained ModelRatio 5.15 and CompletionRatio 6.0 because the
  audit found a critical `price_below_upstream_input` condition. Other 5.6
  variants had no trusted dated actual cost and also retained their prices.
- PackAPI and Unity2 remain disabled and outside the active upstream collection.

## Final Verification

- Ten consecutive read-only production rounds passed. Each round checked the
  container and both HTTPS status endpoints, cron uniqueness, timezone, ledger,
  audit, monitor materialization, PostgreSQL Beijing partition, exact database
  price values, pricing backup, and the 5.6 safety block.
- A normal-user Playwright session rendered `/channel-health` with model name,
  request count, success rate, average response time, and output speed. It
  displayed `北京时间 01:30:43 更新` and contained neither channel names nor
  gross-margin data.
- The browser performance request returned HTTP 200 with only `model_name`,
  `request_count`, `success_rate`, `avg_latency_ms`, and `avg_tps`; browser
  console errors were zero.
- The temporary common-user access token was cleared and verified `NULL` after
  browser validation.
