# Issue173 production deployment

2026-09-24. User explicitly selected A for the native wallet integration upgrade. Production is enabled; not merely a submitted test.

## Verified runtime

- Native: `new-api-fixed:issue173-quota-authority`, image `sha256:4d775fa39d260f9769fca9a53f22e7dd98b774b29c9781312aed8013640d2341`.
- Public frontdoor: `xtai/public-video:issue173`, image `sha256:3f83d18f1ffcfb4dd0785de9e1ff476e114b17da286a5ff50f945e2a51f311f6`.
- Private execution: `xtai-video-public-execution`, existing verified Nody gateway image, independent `/opt/xtai/state/public-video-execution/data`; downstream-specific webhook disabled.
- Main native source baseline: `72d5a71c807a8aab2be3c8f1417169416cd39325`, backported exact files, not unrelated current-repo backend changes.
- Native and public API-key wallet paths use `QUOTA_DB_AUTHORITATIVE=true`; native batch updates disabled. Final account accumulators use actual64-bit ledger bounds, distinct from bounded per-request charges.
- Existing video shared-service key, sub-domain and callback behavior remain unchanged.

## Production evidence

- `GET /v1/models` with ordinary inherited-default key listed seven new videos plus Flare/Sunburst.
- Default, 文, 图, 图香蕉, 视频, 视频放大 and auto key groups returned seven video capabilities.
- Invalid key401; second user cannot query first user's task (404).
- Public video `vjob_b9db731232f62db98c921bc9a67194f6`: succeeded, settled, ready; exact charge0.675CNY, user and finite-token delta337500. Duplicate request returned same ID with one consume record. Authenticated result200,223280bytes, video and audio tracks present.
- Isolated DB video proof `vjob_3ac92a07542e5fb5d48c5e37acd5b8d2` also settled correctly. Its repeated request remained one charge across process reconstruction.
- Isolated default-group Flare request succeeded in39.3s;225000quota=0.45CNY. Flare/Sunburst exact-name routing uses existing 图 tariff, not the key's unrelated group multiplier.
- Paid acceptance upstream costs: two official-Grok video samples0.45CNY each plus one Flare0.30CNY, total1.20CNY; no recharge.
- Dedicated production test users and keys were disabled after verification; unspent synthetic test credit removed. No real customer's historical balance/price was changed.

## Drain and rollback evidence

Backup: `/opt/ai-api-stack/backups/issue173-public-video-20260924` (private).

Three quiescent snapshots spanning17s showed zero native connections, zero active image jobs, zero active native async tasks, identical user/token totals. Last consume timestamp1790262566 was followed by successful native batch completion at23:09:30. No later unpaid queue existed during cutover. Final pre-cutover dump retained.

A first controlled replacement hit an override-file structure mismatch and automatically rolled back; configuration preflight was corrected before the successful replacement. A historical-large-balance guard was fixed before final native promotion. Nginx was restored between attempts, not left in maintenance.

Old native container retained stopped as `ai-api-stack-new-api-1-rollback-issue173`; original image/compose/configs/dumps retained. Rollback must first stop public admission and account for any active public jobs; do not restore an old DB over new user transactions. Public admission and worker checks fail closed if native authority mode disappears.

Public API routes only on `api.aixingtuyun.com`: video submit/query/content, capabilities, prices and model discovery. The original sub-domain shared-service path is unchanged. Nginx supplies the actual peer IP; personal IP/model restrictions remain enforced. ORM SQL logging is disabled in the new public process to avoid exposing keys/prompts.

## Validation and cleanup

Full model/public-video/middleware test packages passed; relevant service billing/quota tests passed. Full service-suite affinity-cache count failures reproduce on unmodified baseline44fab89d4 and are tracked separately in #174; full-suite green is not claimed.

Isolated native/public/execution containers and the old public log-redaction predecessor were removed after verification. Generated canary database `xtai_video173_canary` was dropped after evidence retention. Production execution, new services and native rollback remain. All secrets stay server-side; none are included here.
