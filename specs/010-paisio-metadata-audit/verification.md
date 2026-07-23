# Verification

## Pre-deployment

- 49 Python unit tests pass.
- Python compilation and `git diff --check` pass.
- The patch applies to the current production script, passes `--check`, and a
  second application leaves the SHA-256 hash unchanged.
- A mocked execution of the patched production copy validates both root and
  `/v1` base URLs and records only `GET /v1/models`.
- AST inspection confirms `build_snapshot()` cannot call the legacy paid
  `probe_channel()` path.
- Mocked production-copy execution confirms Topaz type 58 uses only
  authenticated `GET /video/status`, and account-scoped model visibility wins
  over unrelated internal pricing-group labels.
- Pricing metadata persists only allowlisted model pricing fields and sanitized
  account model names; credentials and unknown fields are not persisted.

## Production

- Rollback backup verified at
  `/opt/ai-api-stack/backups/issue10-paisio-audit-20260723-2146`; the directory
  is mode 0700 and its credentials copy is mode 0600. A post-v1 snapshot also
  preserves the intermediate deployment.
- Deployed source commits `4be3201f` and `a6ce7814`; production source matches
  the uploaded collector and policy. The pre-existing auto-pricing script hash
  remains `ce78d6d...` and exactly matches the rollback backup.
- Manual `2026-07-22` collection completed 11/11 required upstreams with zero
  incomplete required sources. Paisio returned 45 authenticated pricing rows,
  75 account models, group `default`, and complete zero-cost billing logs.
- Manual daily audit reports 11 enabled channels, 11 healthy, and 0 failed.
  Paisio matches 75 configured / 75 discovered models. Topaz uses free
  `GET /video/status` and matches all 31 configured models against 46 advertised.
- Paisio channel logs remained exactly 49 rows with maximum ID 30760 before and
  after collection/audit, proving the monitor did not generate a business call.
- Reconciliation is complete: local billing CNY 78.270516, upstream actual cost
  CNY 47.762002, gross profit CNY 30.508514, and gross margin 0.3898.
- Dry-run and live pricing both selected 7 models and safely skipped 803. The
  database already matched every planned value; the live transaction created a
  mode-0600 backup and atomically rewrote the same correct values.
- `gpt-5.6-sol`: actual input/output CNY 1.03/6.18 per million, base ratio 5.15,
  completion ratio 6, sell price CNY 1.545/9.27 at group ratio 0.15.
- `gpt-image-2`: actual CNY 0.525757 per call, base price 5.25757, sell price
  CNY 0.7886355 at group ratio 0.15. All five frontend groups equal 0.15.
- PackAPI and Unity2 channels remain disabled (`status=2`) and both names are
  absent from active upstream and credential files.
- WeChat Pay validation passes for enablement, required values, field formats,
  32-byte API v3 key, and readable private/public key files. Its HTTPS callback
  route is reachable and rejects an empty invalid callback with HTTP 400; no
  real order or payment was created.
- Test user `test_048a0728` exists and is enabled in the `default` group.
- Public `/api/status` is HTTP 200 with `success=true`; NewAPI and database are
  healthy. Channel Monitor pages remain Basic-Auth protected (401 unauthenticated),
  while `/channels?action=create` and `/channel-health` return HTTP 200.
- The 08:20 collection, 08:30 audit, and 08:40 pricing cron entries are unchanged.
