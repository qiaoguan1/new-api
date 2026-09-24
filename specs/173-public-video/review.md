<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|---|---|
| Worker | Codex root + independent wallet/security reviewer |
| Issue | #173 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-09-24 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|---|---|---|
| 1 | Blindspots | ✅ FIXED | 4 |
| 2 | Clarity | ✅ PASS | 0 |
| 3 | Maintainability | ✅ FIXED | 1 |
| 4 | Security | ✅ FIXED | 3 |
| 5 | Performance | ✅ FIXED | 1 |
| 6 | Documentation | ✅ PASS | 0 |
| 7 | Style | ✅ PASS | 0 |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Critical | Sidecar Redis deltas race native cache refill/batch writes | Removed delta outbox; coordinated native database-authoritative reads/writes; native mode checked before each new reserve and worker reconciliation |
| 2 | Major | Native check-then-debit could race video reservation | Conditional SQL preconsume helpers; final supplement still permits real debt |
| 3 | Major | Old worker can overwrite final state | CAS update restrictions; final monetary transaction idempotent |
| 4 | Major | pending_review never resumes | Continue read-only reconciliation and permit proved final settlement |
| 5 | Major | A replay400 could imply erroneous full refund | Resolve original deterministic request first;400/413 retain pending evidence, never infer zero |
| 6 | Major | Token unlimited/finite switch changes delta semantics | Always keep token quota ledger consistent with native accounting |
| 7 | Major | New reserve ignores gateway price/availability | Preflight exact CNY price and available model before reserve; replay uses frozen receipt |
| 8 | Minor | Blank prompt and malformed catalog | Normalize/validate before money, schema mismatch503 |
| 9 | Major | Catalog per-model polling multiplies latency | Fetch one readiness/capability snapshot per listing with bounded JSON/native timeouts |

### Findings Deferred (With Tracking Issues)

| Finding | Tracking | Reason |
|---|---|---|
| Existing service-suite affinity-cache test shared-state failure | #174 | Reproduced on unmodified44fab89d4 baseline; unrelated to this feature. Isolated affinity tests and relevant billing tests pass. |

### Summary

| Category | Count |
|---|---|
| Fixed in PR | 9 |
| Deferred (with tracking) | 1 |
| Unaddressed | 0 |

Full model/public-video/middleware packages pass. Relevant service billing/quota tests pass; full service suite is explicitly NOT claimed green because of #174. Exact deployed native baseline72d5a71c is backported, not replaced with unrelated current-repository code. Isolated DB real-video acceptance completed: success, settled0.675CNY, user+token delta337500, one request, replay sameID, cross-user404, playable MP4 with audio. Native drain, old-batch flush and production acceptance remain deployment gates.

**Review Status:** ✅ COMPLETE
<!-- REVIEW:END -->
