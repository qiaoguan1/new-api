# Implementation Plan: Complete Video Billing Settlement

## Summary

Replace the gateway's false “actual cost” result with a durable billing state machine and an
authenticated exact-evidence ingestion contract. Reuse the same states and exact quota arithmetic in
NewAPI task billing, while adding controlled wallet overdraft and debt gating for ordinary users.

## Technical Context

- Go 1.22+, Gin, GORM; SQLite/MySQL/PostgreSQL and optional Redis/batch quota cache.
- Python 3.11+, standard-library HTTP server and SQLite WAL video gateway.
- Provider evidence is collected by `ops/channel-monitor`; provider credentials remain outside Git.
- Money is decimal CNY; NewAPI quota conversion is `500000 quota/CNY`.

## Constitution Check

- Evidence first: no catalog price or time-window inference may settle a successful task.
- Durable idempotency: reservation and settlement revisions survive retry/restart.
- Fail closed: missing/conflicting evidence never becomes zero cost.
- Public privacy: only user-side amounts and stable task states leave the private boundary.
- Cross-database: Go persistence uses GORM abstractions and additive fields.
- Test first: RED tests precede each implementation slice.

## Delivery Slices

1. Freeze official x1.5 reservation and remove dynamic price override.
2. Add gateway billing columns, settlement revisions, private evidence ingestion, and redacted query
   responses.
3. Export exact reconciliation rows from channel monitor into the gateway settlement contract.
4. Add transactional NewAPI task settlement fields and public usage states.
5. Add wallet single-task cross-zero reservation, debt gate, and hard-limit exclusions.
6. Run focused/full suites, security review, ten deterministic rounds, PR and CI.

## Rollout

Additive schema migration → dark image and copied-database test → monitor dry run without evidence
write → authenticated evidence write in shadow/audit mode → one non-paid fixture → bounded production
activation. Do not deploy or create paid provider tasks from automated tests.
