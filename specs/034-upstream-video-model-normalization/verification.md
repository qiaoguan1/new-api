# Production Verification

**Date:** 2026-08-09 (Asia/Shanghai)
**Issue:** [#34](https://github.com/qiaoguan1/new-api/issues/34)

## Deployment

- Production root: `/opt/ai-api-stack/channel-monitor`
- Release copy: `/opt/ai-api-stack/releases/video-model-normalization-20260809-issue34`
- Rollback backup:
  `/opt/ai-api-stack/channel-monitor/backups/video-catalog-issue34-20260809-172411`
- Schedule: minute 10 of every hour under `CRON_TZ=Asia/Shanghai`, guarded by
  `/run/lock/upstream-video-catalog.lock`.
- The deployment did not edit the NewAPI database, channel rows, price options,
  user quota, or the downstream desktop application.

## Final collection

| Check | Result |
|---|---:|
| Enabled channels checked | 13 |
| Complete upstream catalogs | 10 |
| Failed catalogs preserved/fail-closed | 3 |
| Raw catalog models read | 2,319 |
| Relevant Seedance 2.0 entries retained | 21 |
| Deterministically matched catalog entries | 17 |
| Review-required names | 4 |
| Enabled and healthy configured routes | 5 |
| Stable SKUs with trusted actual cost | 1 |
| Publishable candidate routes | 1 |

The only candidate route that passed every gate is:

- Stable SKU: `seedance-2.0` + `720p`
- Upstream route: Paisio channel 42, raw model
  `seedance2.0-selfsur-720p`
- Price evidence: complete actual billing on 2026-08-08, 62 calls,
  CNY 0.168387 per call, normalized from reviewed alias `sd2-720p`.

Rolldek has three enabled/healthy recognized Seedance 2.0 routes but no positive
actual deduction evidence on the target day or lookback window, so none entered
the candidate publish manifest.

## Review queue

The following Rolldek names remain hidden and include untrusted suggested
mappings for operator review:

- `seedance-2.0-431-480p`
- `seedance-2.0-431-720p`
- `seedance-2.0-fast-431-480p`
- `seedance-2.0-fast-431-720p`

The `431` marker is not assumed to mean a known quality/tier. Suggestions have
`requires_review: true` and cannot publish.

## Non-blocking collection failures

- CodePlan channel 38 returned no valid model IDs.
- CodePlan channel 39 could not establish the current HTTPS connection.
- Channel 43 returned HTTP 404 for `/v1/models`.

None is a configured Seedance 2.0 route. A failed observation does not replace
that channel's last complete snapshot.

## Verification results

- 33 channel-monitor unit tests passed.
- Python compilation and `git diff --check` passed.
- Ten deterministic production verification rounds passed with stable hashes,
  one candidate route, four review entries, and one privacy-safe public model.
- All 13 enabled channel API keys were checked against every generated runtime
  JSON file; zero keys were present.
- Public capabilities contain only `xtai-relay-v1`, catalog revision,
  `seedance-2.0`, `720p`, and availability. No source, raw model, channel ID,
  cost, margin, credential, or review metadata is present.
- NewAPI, PostgreSQL, and Redis were all `running` and `healthy` after deployment.
- Root filesystem usage was 50% with 48 GB available.

## Rollback

The backup contains the pre-deployment root crontab and any pre-existing target
files. Rollback consists of removing the marked `UPSTREAM VIDEO CATALOG` cron
block, restoring backed-up files where present, and moving the new issue-34
files/data candidates out of the runtime directory. No database rollback is
required because the deployment made no database writes.
