# Production Verification: Paisio, Pricing Isolation, and Daily Digest

**Verified**: 2026-08-12 (Asia/Shanghai)  
**Issue**: #67  
**Implementation PRs**: #68, #69

## Deployment

- NewAPI image: `new-api-fixed:ops-digest-872067ba-r2`, healthy.
- Video gateway image: `xtai/video-job-gateway:ops-62f14af1`, running with persistent SQLite state.
- Channel-monitor scripts, balance alerts, authorization lifecycle, and Beijing-time cron schedules are installed.
- The Nginx video gateway network alias was corrected to `video-job-gateway-v2-production`; `nginx -t` passes and the active `/v1/videos` location proxies to the gateway without an incident `503` guard.

## Paisio evidence and routing

- Ten recent successful Paisio tasks reconciled 10/10 through exact authenticated `task_id` and `request_id` lookups.
- Reprocessing the 2026-08-10 ledger produced 22/22 exact task evidence rows totaling CNY 12.47, equal to the authenticated daily billing ledger.
- Paisio and Toonflow are eligible for new v2.1 tasks; RollDek remains excluded because it does not yet have approved exact task-cost evidence.
- For standard and fast resolutions where both routes exist, Paisio priority 10 precedes Toonflow priority 20. Mini remains Toonflow-only.

## Pricing isolation and reporting

- The live automatic-pricing run completed with 1,186 discovered models, 0 writes, and 1,186 safe skips. Incomplete Apikeyfun/CodePlan credentials no longer abort unrelated model/channel decisions.
- Video models remain protected by the separate official-price policy.
- The daily digest for 2026-08-11 was delivered to the fixed configured recipient with 18 channel rows. A second invocation returned `already_delivered`; its state file is mode `0600`.
- The initial full digest exposed an SMTP long-line failure. PR #69 adds bounded line wrapping and a maximum-payload regression test; the production resend succeeded.

## Ten-round read-only verification

All 10 rounds passed. Every round checked:

- NewAPI health and exact production image.
- Gateway health and Paisio/Toonflow eligibility.
- Paisio-first route ordering where supported.
- Beijing-time cron uniqueness for balance collection, balance alerts, automatic pricing, and daily digest.
- Delivered digest state and file permissions.
- Latest live automatic-pricing completion and 1,186 isolated decisions.
- Active Nginx video route and `nginx -t`.
- SQLite quick check, unique request IDs, v2.1 markup invariants, stale settlement backlog, and Webhook backlog.

Final database counters were all zero: duplicate requests, invalid markups, settlements pending over 30 minutes, Webhooks pending over 10 minutes, and failed Webhooks.

No paid generation task was submitted during verification.
