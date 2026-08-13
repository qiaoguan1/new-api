# Implementation Plan: Unattended Video Failure Recovery

## Architecture

Introduce a durable attempt ledger instead of overloading the single provider fields on `video_jobs`.

### Tables

- `video_job_attempts`: one immutable route attempt with lifecycle and provider identities.
- `video_attempt_evidence`: unique authoritative cost/refund evidence.
- Existing `video_jobs` remains the downstream aggregate and compatibility projection.

### State machine

`prepared -> submitting -> running -> reconciling -> succeeded|failed_zero|failed_costed`

The job remains internally active while any attempt is reconciling. Route advancement is an atomic transaction that closes the previous attempt and prepares exactly one next attempt.

### Settlement

Each evidence row stores exact CNY. Final charged amount is `sum(attempt net cost) * 1.5`, quantized once to six decimals. Evidence fingerprints enforce idempotency.

### Provider adapters

- Add reconciliation interfaces independent from generation adapters.
- Reuse the same restricted credential files already mounted in the container.
- Provider-specific code emits typed decisions: found/running/succeeded/terminal_cost/not_found_authoritative/ambiguous/retry.

### Safety

- Max attempts: 4 (the current Fast 720p route plan has four candidates).
- Max total recovery age: 8 hours.
- Extra-cost guard defaults to the original Ark reservation.
- Ambiguous evidence never becomes zero.
- Public responses redact provider identity and evidence payloads.

## Delivery

1. Sync production-leading Paisio identity binding and SD3/SD4 catalog into source.
2. Add failing store/state-machine tests.
3. Add failing provider reconciliation tests.
4. Implement attempt ledger and reconciliation loop.
5. Change final settlement to aggregate attempts.
6. Update API/docs and operational metrics.
7. Full tests, comprehensive/security review.
8. Candidate database migration on a copied SQLite file.
9. Drained production deployment, rollback backup, ten no-paid rounds.
