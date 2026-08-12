# Production Verification: Video Multi-Channel Auth Lifecycle

**Verified**: 2026-08-12 (Asia/Shanghai)  
**Merged gateway release**: `b69f9f24`  
**Production image**: `xtai/video-job-gateway:auth-b69f9f24`

## Deployment

- Production SQLite was backed up online to
  `/opt/xtai/backups/issue63/20260812-192154/video-jobs.sqlite3` before switching.
- The prior container was retained stopped as
  `xtai-video-job-gateway-v2-production-rollback-20260812-192154`.
- `/data` remains a persistent bind mount; `/run/secrets/video-billing` is read-only.
- The current release link points to
  `/opt/xtai/releases/video-auth-b69f9f24/ops/video-job-gateway`.
- The hourly Beijing-time authorization job is installed once at minute 17 under `flock`.

## Channel readiness

| Provider | Generation | Billing auth | Independent audit | New v2.1 traffic |
|---|---:|---:|---:|---:|
| Toonflow | ready | ready | approved | eligible |
| Paisio | ready | ready | blocked: prior `cost_mismatch` | excluded |
| RollDek | ready | ready | blocked: no terminal video evidence/catalog route | excluded |

All three providers are registered, refreshed and visible on the authenticated health endpoint.
Registration never bypasses the independent actual-cost audit gate. PackAPI and Unity2 remain absent.

## Authorization lifecycle

- Paisio and RollDek read-only account sessions refreshed successfully and were written as mode-0600
  files owned by the non-root gateway UID.
- Toonflow's CAPTCHA-bound token is valid until 2026-09-09 16:25:43 Beijing time. It is reloaded
  from a mode-0600 file and has 30/14/7/3/1-day deduplicated warnings; no CAPTCHA bypass exists.
- A real 30-day expiry notification was delivered through the production mail path. The legacy
  notification compatibility fallback succeeded without including credentials.

## Automated verification

- Gateway unit/protocol/security tests: 68 passed.
- Channel-monitor tests: 190 passed after the notification compatibility follow-up.
- Go controller/router tests and `go vet`: passed.
- Production build/import checks: passed.
- Ten consecutive read-only production rounds passed. Each round ran 15 assertions covering:
  container/image health, exact three-provider registration, Toonflow-only audit approval, all three
  billing credentials, safe exclusion reasons, persistent data, read-only secrets, 0600 modes,
  single cron entry, current release link, Nginx syntax and ledger invariants.
- All ten rounds reported: successful pending settlements `0`, undelivered Webhooks `0`, duplicate
  settlement IDs `0`, markup violations `0`, durable jobs `21`, settlements `17`.
- No paid generation request was submitted during verification.

## Result

Production is healthy. All requested video providers are managed by the gateway and authorization
lifecycle, while only providers with independently approved exact-cost evidence can receive new work.
