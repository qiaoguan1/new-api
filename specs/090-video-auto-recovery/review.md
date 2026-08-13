<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | `codex-root` |
| Issue | #90 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-08-13T18:10:00+08:00 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | FIXED | 2 |
| 2 | Clarity | PASS | 0 |
| 3 | Maintainability | PASS | 0 |
| 4 | Security | FIXED | 1 |
| 5 | Performance | PASS | 0 |
| 6 | Documentation | FIXED | 1 |
| 7 | Style | PASS | 0 |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | SQLite numeric aggregation could pass fixed-point money through binary floating point. | Aggregate attempt costs are now validated and summed with Python `Decimal`. |
| 2 | Major | Recovery deadline still emitted `pending_review`, leaving a per-job operator decision. | Deadline now deterministically fails the downstream job and releases its reservation while retaining a safe operational error code. |
| 3 | Major | Terminal failed jobs could emit a full-refund webhook immediately before a non-zero settlement webhook. | Failed jobs with verified attempt evidence emit `settlement_pending` first and one authoritative final settlement afterward. |
| 4 | Minor | Existing routing documentation described manual reconciliation. | Gateway and downstream protocol documentation now describes the bounded automatic recovery contract. |

### Security Review

- Credentials remain server-side and are loaded from existing restricted files or environment.
- Provider responses, raw ledgers, task mapping, margins, and credentials are not added to public snapshots.
- SQL statements remain parameterized; evidence identity is unique and state changes use `BEGIN IMMEDIATE` transactions.
- Recovery does not use an external LLM to infer financial state and never fabricates zero-cost evidence.
- Same-route ambiguous submit replay preserves the exact idempotency key; cross-route advancement requires authenticated terminal billing evidence.

### Findings Deferred (With Tracking Issues)

None.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 4 |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE
<!-- REVIEW:END -->
