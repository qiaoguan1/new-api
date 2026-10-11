# Implementation plan

Use the existing Spec Kit spec/plan/tasks workflow without initialization or
template replacement. A separate Git worktree preserves the unfinished #190
source and does not deploy that candidate.

1. Read production route projections and fresh balance evidence. Fetch only
   sanitized values; keep all authentication material in memory.
2. Build a pure routing manifest. Both native channels and abilities must be
   updated; do not use general channel PUT or `/fix`, which rebuild abilities.
3. Under bounded PostgreSQL locks, validate the exact before hash and row sets,
   update only approved fields, and validate the after hash before commit.
   Hanhe model alignment and priority changes are in the same transaction.
4. Journal intent before sending SQL. Unknown transport outcomes are reconciled
   by read-only inspection, never retried as a second mutation.
5. Wait for the actual native cache period. If necessary, clear only affected
   affinity rules through the existing authenticated admin cache interface;
   preserve affinity configuration and customer sessions.
6. Verify readback, unrelated state, health and non-generating directory access.
   Do not infer paid output verification from directory or container health.

Evidence of provider funds is dated and provider-specific; display quotas are
not automatically RMB/USD cash. Haina is explicitly prioritized by the user
while retaining its manual-evidence policy. Other unknown providers are not
silently classified as exhausted.
