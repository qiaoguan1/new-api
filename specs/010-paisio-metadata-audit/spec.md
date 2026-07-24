# Feature Specification: Zero-Cost Authenticated Upstream Audit

**Issue**: [#10](https://github.com/qiaoguan1/new-api/issues/10)

## User Outcome

Daily channel auditing must use authenticated read-only metadata when an upstream
account is available. A mismatched live generation probe must not mark Paisio
unhealthy, create paid usage, or pollute business error counts.

## Acceptance Criteria

- The classic account collector saves a sanitized authenticated pricing catalog
  alongside the dated, complete actual-cost ledger entry.
- The daily audit validates a channel key through `GET /v1/models`.
- Availability is based on configured, advertised, priced, and group-enabled model
  intersection.
- No daily availability scan calls any generation endpoint; every channel uses
  read-only model catalog metadata, even when account metadata is unavailable.
- Missing or incomplete actual-cost collection remains fail-closed for auto pricing.
- PackAPI and Unity2 remain outside active upstream definitions and credentials.
- Production scripts and data are backed up before deployment.
- Yesterday's collection, audit, reconciliation, and pricing are rerun and verified.

## Non-Goals

- No paid chat, image, or video task.
- No automatic deletion of historical PackAPI or Unity2 report rows.
- No price invention for models without trusted actual billing cost.
