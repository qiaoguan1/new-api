# Implementation Plan: Production Deployment and Upstream Audit

## Phase 1: Reproduce the Production Baseline

1. Create an isolated worktree at `4cca558c`.
2. Confirm the default frontend layout and current DOMPurify version.
3. Build the unchanged baseline with locally cached Docker bases where practical and record identity.

## Phase 2: Port Only PR #136 Runtime Outcomes

1. Add the OAuth callback-mode regression test under `web/default/` and observe RED.
2. Port the helper and two call-site integrations to `web/default/`.
3. Update DOMPurify to 3.4.13 and regenerate the matching lockfile.
4. Run focused and complete frontend verification plus Docker build.
5. Record a production-baseline patch commit for reproducibility.

## Phase 3: Backup and Activation

1. Capture current image ID, container inspect, compose files, public invariants, and source patch.
2. Run the installed daily recovery backup and verify its checksums/restore listing.
3. Transfer the reviewed production-baseline source archive to a private release directory.
4. Build an issue-specific image without changing the current tag.
5. Update only the compose `new-api.image`, validate compose, recreate only `new-api`, and wait for
   container health.
6. Run five public no-charge health rounds; restore compose and prior image immediately on failure.

## Phase 4: Upstream Audit

1. Use existing channel-monitor control-plane scripts and stored sessions without printing secrets.
2. Refresh no-charge authentication/balance/catalog evidence where supported.
3. Read database/channel state through bounded metadata queries with keys excluded.
4. Rebuild the model/channel health report only if the existing scripts are explicitly dry-run/read-only.
5. Classify every provider/channel as healthy, degraded, quarantined, disabled, or unknown.

## Rollback

Restore the backed-up compose files, run `docker compose config --quiet`, recreate only `new-api`,
verify the original image ID and five public health rounds, and leave all data volumes untouched.
