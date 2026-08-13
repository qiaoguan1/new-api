# Issue #60 — XingTu v2.1 staging handoff

## Goal

Bring the isolated staging gateway to production code parity without copying its API token or
Webhook secret into source control. Verify the downstream staging callback with a signed,
idempotent replay of an existing non-billable terminal event.

## Requirements

- Production and staging retain different API tokens, Webhook URLs, Webhook HMAC secrets, and
  state directories.
- Staging runs the current v2.1 gateway image and the same provider/billing capabilities as
  production, using server-side read-only secret files.
- Existing staging state is migrated in place after an online SQLite backup.
- Upgrade is refused while staging has active or pending-settlement jobs.
- Health, readiness, schema migration, request-size limit, audio contract, and billing collector
  readiness are verified without creating a paid task.
- A previously delivered staging event is re-signed with the staging secret and replayed to the
  staging callback. A 2xx response proves signature verification and duplicate-event idempotency.
- Rollback restores the previous container and state backup.

## Non-goals

- Do not modify downstream wallet, canvas, or point accounting.
- Do not submit a new image or video generation task.
- Do not expose upstream/provider identities in the public contract.
- Do not store credentials, hashes of credentials, or event payloads in Git or GitHub.
