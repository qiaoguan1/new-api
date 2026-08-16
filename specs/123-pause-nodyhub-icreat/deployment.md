# Production deployment record

## Change

- Deployed: 2026-08-16 14:31 CST.
- Target channels: 27, 28, 40, and 41.
- Channels 27 and 28 were already status 2; channels 40 and 41 changed from
  status 1 to status 2.
- All 1,155 target ability rows were set to `enabled=false`.
- No key, model list, priority, weight, price, log, task, ledger, or monitoring
  record was deleted or changed.

## Safety evidence

- Target usage in both the preceding hour and 24 hours was zero.
- No NewAPI task referenced any target channel.
- The standalone video gateway uses Paisio, Rolldek, and Toonflow, not NodyHub
  or iCreat.
- Private rollback backup:
  `/opt/ai-api-stack/backups/issue123-pause-nodyhub-icreat-20260816-143026`
- The backup contains a full PostgreSQL dump, a target snapshot, and unrelated
  state hashes, all mode 0600 under a mode-0700 directory.

## Verification

- All four target channel rows have status 2.
- Enabled ability count is zero for every target channel.
- Hashes and row counts for all unrelated channel statuses and abilities are
  unchanged.
- NewAPI reloaded its database channel cache and is healthy.
- Five public health rounds returned HTTP 200 for the site and API status.

## Current routing summary

- Enabled NewAPI channels: 10 of 33.
- Text: Code Plan, Hanhe, JojoCode, and Maolao.
- Image: Maolao, Hanhe, and JojoCode.
- Video and media: Paisio video, Paisio banana, and Topaz upscale.
- The standalone video gateway currently admits Paisio and Toonflow; Rolldek
  remains excluded from new jobs.

