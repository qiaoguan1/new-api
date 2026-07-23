# Verification

## Local and Review Gates

- 34 Channel Monitor Python tests passed.
- 2 focused TypeScript entry-action tests passed.
- Targeted ESLint and Prettier checks passed.
- A clean `bun install --frozen-lockfile`, `bun run typecheck`, and `bun run build`
  passed using the same dependency mode as the production Dockerfile.
- The standalone patch was exercised on a copy of the production HTML: initial apply,
  check-only verification, and repeat apply all passed.
- The seven-criterion comprehensive review and security review were posted to Issue #9;
  both findings were fixed and the unaddressed count is zero.

## Production

- Rollback backup created at
  `/opt/ai-api-stack/backups/issue9-channel-entry-20260723-2125`.
- Commit `f1999293` was built as
  `new-api-fixed:channel-entry-20260723-f1999293` and reached Docker `healthy`.
  The current successor image `new-api-fixed:channel-names-20260723-0429a772`
  preserves the same entry implementation and is also healthy.
- `/channels?action=create` returns HTTP 200 and maps to the existing create
  dialog through the tested `initialDialog` action.
- `/channel-health` returns HTTP 200. The embedded binary contains both the
  create action and the two-step channel setup guide.
- Standalone `/channel-monitor/` and `/channel-monitor/upstreams-admin.html`
  remain Basic-Auth protected and return HTTP 401 without credentials.
- The standalone toolbar contains explicit Add Channel, NewAPI Channels,
  Monitor Configuration, and Upstream Collection Configuration entries.
- Production cron entries remain 08:20 collection, 08:30 audit, and 08:40
  pricing; no permissions or scheduling changes were introduced.
