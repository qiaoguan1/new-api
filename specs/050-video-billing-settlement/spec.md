# Feature Specification: Complete Video Billing Settlement

**Feature Branch**: `codex/issue-50-video-billing-settlement`
**Created**: 2026-08-11
**Status**: In Progress
**Issue**: #50

## Goal

Make video billing follow one auditable rule for XingTu software and ordinary registered users:
reserve Ark official cost multiplied by 1.5, then settle a successful task from trustworthy
single-task provider net-cost evidence multiplied by 1.5. The provider route and provider cost
remain private.

## User Scenarios & Testing

### P1 - Safe fixed reservation

A request with a supported stable model, resolution, and duration receives a frozen Ark-official
x1.5 reservation before provider submission. Provider catalog prices and selected routes cannot
change that reservation.

### P1 - Evidence-first final settlement

A successful task remains `settlement_pending` until an exact provider/task ledger record is
available. Applying the evidence once refunds or supplements the frozen reservation; replaying it
does not move money twice.

### P1 - Controlled wallet debt

An ordinary wallet user whose balance is positive when a task is accepted may have that one task
cross the balance below zero during reservation or final supplement. A non-positive balance blocks
all later tasks until recharge makes it positive. Subscription and hard-limited token quota never
overdraft.

### P2 - Provider-neutral downstream response

XingTu software and ordinary users can query task and billing state, reserved/final/refund/supplement
amounts, and result delivery state without receiving provider identity, raw model, upstream task ID,
actual provider cost, margin, credentials, or route order.

## Requirements

- **FR-001**: Video reservation MUST come only from the reviewed Ark official table multiplied by
  exactly `1.5`; dynamic provider/model marketplace prices MUST NOT override it.
- **FR-002**: The request MUST persist the exact reservation, official revision, duration, and
  billing contract before provider submission.
- **FR-003**: A successful task without trustworthy evidence MUST expose
  `succeeded + settlement_pending`; it MUST NOT call the reservation an actual cost.
- **FR-004**: Automatic success settlement MUST require an exact provider and upstream task-ID
  match, a terminal provider record, a non-negative finite CNY net cost, and a unique evidence
  fingerprint.
- **FR-005**: Final user charge MUST equal provider net cost multiplied by exactly `1.5`, converted
  with `1 CNY = 500000 quota` and rounded up once at the quota boundary.
- **FR-006**: Explicit provider failure MUST refund the complete reservation exactly once.
- **FR-007**: Missing, ambiguous, inferred-only, incomplete, or conflicting evidence MUST keep the
  reservation and enter `settlement_pending` or `pending_review`.
- **FR-008**: Settlement MUST be transactional and idempotent across retries, concurrent pollers,
  process restarts, and evidence replay.
- **FR-009**: An ordinary wallet request MAY cross below zero only when the balance was positive at
  acceptance. Once non-positive, subsequent requests MUST fail with `account_in_debt`.
- **FR-010**: Subscription quota and a token configured with a finite hard limit MUST remain
  non-negative and reject insufficient reservations or supplements.
- **FR-011**: Recharging a wallet MUST naturally offset debt; new tasks resume only when the
  authoritative wallet balance is positive.
- **FR-012**: Public responses MUST implement `reserved`, `settlement_pending`, `settled`,
  `settled_with_debt`, `refund_pending`, `refunded`, `payment_required`, and
  `pending_review` where applicable.
- **FR-013**: Public responses and logs MUST NOT expose provider IDs, raw upstream model names,
  upstream task IDs, provider actual cost, margin, credentials, or raw evidence.
- **FR-014**: CLR and non-video billing behavior MUST remain unchanged.
- **FR-015**: Database changes MUST remain compatible with SQLite, MySQL, and PostgreSQL.

## Edge Cases

- A zero-cost provider ledger row is valid only when explicitly marked `zero_verified`.
- An evidence replay with the same fingerprint returns the prior settlement; the same revision with
  different content is a conflict.
- A later provider refund/reversal becomes the next settlement revision and applies only the delta
  from the previous final charge.
- Result data may exist internally while settlement is pending, but the public result remains held.
- A task already accepted continues to completion and settlement even if the wallet becomes negative.
- If token adjustment fails after wallet adjustment, the transaction must roll back or remain
  recoverable; it must not report `settled`.

## Key Entities

- **Video Billing Snapshot**: immutable reservation and official price revision captured at submit.
- **Provider Cost Evidence**: private exact task-level provider ledger record and fingerprint.
- **Settlement Revision**: idempotent calculation and money movement for one evidence revision.
- **Wallet Debt State**: derived from authoritative wallet balance; it is not a manually editable flag.

## Success Criteria

- **SC-001**: All focused Go and Python billing tests pass, including concurrent/replayed settlement.
- **SC-002**: Ten deterministic request/query/settlement rounds produce one reservation and one final
  balance movement per request.
- **SC-003**: Secret and public-response scans find zero provider/cost/margin leakage.
- **SC-004**: Existing video routing, failure fallback, monitor, task billing, and CLR regression suites
  remain green.

## Assumptions

- The reviewed Ark table in `ops/video-job-gateway/relay-pricing.json` is the reservation source.
- Channel monitor continues to authenticate to providers and is the only automatic evidence producer.
- XingTu cloud applies the signed private settlement contract to its own account ledger; this
  repository owns the relay contract and the NewAPI registered-user ledger behavior.
