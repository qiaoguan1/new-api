# Implementation Plan

1. Inventory successful historical usage, retained pricing parameters, ledger
   dates/models, completeness markers, billing kinds, users, and tokens.
2. Add failing unit tests for Beijing-day matching, trusted evidence selection,
   token/cache/fixed calculations, positive-difference filtering, and
   idempotency-plan generation.
3. Implement a read-only planner that emits deterministic request/user reports
   and a separate guarded executor that consumes the exact plan.
4. Make the executor transactional and idempotent, add refund audit rows/logs,
   reconcile user/token quota, and target quota-cache invalidation.
5. Run focused and full tests plus comprehensive review; correct every blocking
   finding before production use.
6. Back up production data, run and manually reconcile the dry-run, execute the
   frozen plan once, verify all affected users and totals, rerun for zero, then
   perform ten validation rounds.

## Safety Strategy

The planner fails closed at the request level when cost provenance, billing
kind, model normalization, or original billing inputs are incomplete. The
executor refuses an unfrozen or changed plan, takes a PostgreSQL advisory lock,
checks aggregate invariants before mutation, and commits balances and unique
refund audit records atomically. No credential or raw token is written to a
report.
