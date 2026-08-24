# Production Deployment Specification: Issue 137

**Issue**: #137
**Created**: 2026-08-25
**Status**: Deployed and Verified

## Goal

Deploy the two reviewed runtime outcomes from PR #136 to the actual production source baseline,
then produce a no-charge status for every configured upstream.

## Verified Baselines

- GitHub reviewed merge: `4336a72bd159d3f34cf37d5edb99be3fd0a385e1`.
- Production image: `new-api-fixed:patrol-repair-4cca558c`.
- Production source object: `4cca558c70734dc5d81df5cab0949d4da857f37c`.
- Production frontend layout: `web/default/`; it is not ancestry-compatible with current `main`.

## Requirements

1. The deployment MUST start from production source `4cca558c`, not current `main`.
2. Only the OAuth foreign-opener callback proof and DOMPurify 3.4.13 changes from PR #136 MAY be
   ported into `web/default/`.
3. OAuth regression tests MUST fail before the port and pass afterward.
4. The complete production-baseline frontend tests, typecheck, lint, lockfile check, and Docker build
   MUST pass before activation.
5. A private backup MUST contain the current compose files, container/image metadata, database
   recovery bundle, and exact rollback image/tag before activation.
6. Activation MUST replace only the `new-api` service and preserve databases, mounted data, secrets,
   routes, prices, channels, video gateways, and other containers.
7. Public status and video readiness MUST pass immediately after activation; failure triggers rollback.
8. Every configured upstream MUST receive a current status from no-charge authentication, balance,
   catalog, monitoring, and routing evidence. Unknown evidence remains fail-closed.

## Safety Boundaries

- No full-main build or unrelated source change.
- No credential values in commands, logs, Issue comments, or artifacts.
- No paid generation or content submission.
- No price, route, key, provider allowlist, or database mutation during upstream inspection.
- No Docker publication or release workflow.

## Success Criteria

- Production uses the issue-specific image built from `4cca558c` plus exactly the two reviewed ports.
- Five consecutive public health rounds pass with video `accepting=true` and circuit closed.
- The prior image can be restored with one compose-file restore and service recreation.
- The upstream report covers every configured channel or marks it unknown with a concrete reason.

## Outcome

- Production image: `new-api-fixed:issue137-eb65d7032`
  (`sha256:63a028e3d11e974169db3c6b34e1e09d6209ac3b2e54628e9e04ac50aaa0c11d`).
- Reproducible production-baseline commit: `eb65d7032` on
  `codex/issue-137-production-baseline-patch`.
- Activation changed only the `new-api` service; every other container ID remained unchanged.
- Five activation rounds and three independent public rounds passed. Video remained accepting with
  its circuit closed.
- The no-charge upstream audit covered all 34 configured channels: 12 enabled and catalog-healthy,
  22 disabled and not live-probed.
- Detailed evidence is recorded in `deployment-record.md` and `upstream-report.md`.
