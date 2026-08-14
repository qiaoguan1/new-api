# Plan

1. Freeze production consumption, channel inventory, upstream ledger, and
   existing-refund baselines without changing balances.
2. Add a source-bound refund planner and regression tests around incomplete
   sources, model kinds, fingerprints, and idempotency.
3. Generate a production dry-run plan, manually reconcile every affected user,
   and publish the exact plan hash and review artifact to Issue #111.
4. Run focused and complete channel-monitor tests plus transaction rollback
   validation.
5. Back up affected users, tokens, source logs, and the database; stop public
   ingress; apply the frozen plan in one transaction; invalidate affected caches;
   restore ingress.
6. Verify wallet deltas, audit rows, refund logs, second-run idempotency, and ten
   no-charge service health rounds.

## Rollback

If the transaction fails, PostgreSQL rolls it back and no cache invalidation is
attempted. If a post-commit verification fails, preserve the immutable refund
audit and restore using the timestamped database/row backup through a separately
reviewed compensating transaction; never delete ledger or audit evidence.
