# Verification: Upstream Credential and Cost Coverage Repair

**Production day**: 2026-07-22  
**Executed**: 2026-07-23 (Asia/Shanghai)

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
