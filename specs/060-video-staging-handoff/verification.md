# Verification

Verified on 2026-08-13 (Asia/Shanghai).

- Production and staging API tokens, HMAC secrets, callback URLs, and state mounts are independent.
- Production callback: `https://ds.aixingtuyun.com/auth/api/webhooks/video`.
- Staging callback: `https://ds.aixingtuyun.com/auth-video-v2-staging/api/webhooks/video`.
- Staging online SQLite backup completed before migration; previous container retained as
  `xtai-video-job-gateway-v2-staging-rollback-issue60`.
- Candidate and final staging health/readiness/schema/integrity checks passed.
- Staging now uses `xtai/video-job-gateway:auto-recovery-20260813` and read-only provider billing
  credentials. Paisio and Toonflow are eligible for new v2.1 tasks.
- Existing delivered staging event was HMAC-signed with the staging secret and replayed. Downstream
  returned HTTP 200 for the duplicate `event_id`, verifying signature acceptance and idempotency.
- Ten no-cost verification rounds passed: two eligible providers, zero active jobs, zero pending
  settlements, zero Webhook backlog, and SQLite integrity `ok`.
- No paid task was submitted. Downstream wallet and canvas state were not modified.
