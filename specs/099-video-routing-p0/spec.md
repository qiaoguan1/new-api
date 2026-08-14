# P0 Specification: Restore Safe Video Routing

**Issue**: [#99](https://github.com/qiaoguan1/new-api/issues/99)
**Branch**: `codex/issue-99-video-routing-p0`

## Incident

The 2026-08-14 production image was built from stale staged gateway files. Its
catalog revision `2026-08-14.1` restored Paisio `sd2-*` routes and its runtime
omitted the evidence-backed failed-attempt recovery merged by issue #90.
Three downstream 720p jobs reached Paisio, received provider task IDs, then
failed with `no eligible account: scheduler claim wait timed out`. Toonflow was
never attempted.

## Requirements

1. The production catalog contains no Paisio `sd2-*` route.
2. Standard and Fast routes use the approved Paisio SD3/SD4 models and retain
   Toonflow as the final fallback; Mini remains Toonflow-only.
3. A definite pre-creation rejection advances immediately.
4. A failure after provider task creation advances only after authoritative,
   idempotent failed-attempt cost evidence is persisted.
5. A gateway image build must be bound to a full Git commit, the exact SHA-256
   digest of the catalog, and a deterministic digest of every runtime source
   file copied into the image.
6. Deployment must preserve the production SQLite database, credentials,
   Webhook configuration, and historical task queries.
7. No paid generation is permitted for this hotfix verification.

## Acceptance

- Full gateway tests pass.
- The built image reports the intended commit and catalog digest.
- Staging and production run catalog revision `2026-08-14.2` with no `sd2`.
- Recovery APIs and database migrations are present in the running image.
- Ten no-charge production verification rounds pass.
