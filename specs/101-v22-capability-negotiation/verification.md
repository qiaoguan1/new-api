# Production verification

## Release

- Issue: #101
- Pull request: #102
- Merge commit: `ca5b45f342234c9bcd84b0d3b7d934f45a07be47`
- Catalog revision: `2026-08-14.2`
- Deployment order: isolated staging, then production
- Paid verification requests: 0

## Contract checks

- An authenticated v2.2 capability request returns `xtai-relay-v1`, top-level
  `xtai-video-billing-v2.2`, video-level `xtai-video-billing-v2.2`, and
  `traffic_enabled=true`.
- Explicit v2.1 and absent-header requests preserve the v2.1 response.
- An unsupported contract returns HTTP 400 with
  `unsupported_contract_version`.
- Unauthenticated discovery remains HTTP 401.
- The price catalog remains `xtai-video-pricing-v1`, CNY, seven combinations,
  and `output_second` for every row.

## State checks

- Ten production rounds passed through both public relay hostnames.
- SQLite integrity: `ok`.
- Historical production jobs preserved: 58.
- Active jobs: 0.
- Pending billing jobs: 0.
- Undelivered webhooks: 0.
- Production container restarts after activation: 0.
- Pre-activation database backups and stopped rollback containers were retained.
