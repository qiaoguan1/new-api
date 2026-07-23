<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | `codex/root` |
| Issue | #15 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-07-24T00:43:00+08:00 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | PASS | 0 |
| 2 | Clarity | FIXED | 1 |
| 3 | Maintainability | PASS | 0 |
| 4 | Security | FIXED | 1 |
| 5 | Performance | PASS | 0 |
| 6 | Documentation | PASS | 0 |
| 7 | Style | PASS | 0 |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | The normal-user performance endpoints returned raw database errors. | Detailed failures are now logged server-side while clients receive a generic Chinese availability message; a regression policy test covers both handlers. |
| 2 | Minor | The time-range trigger and summaries displayed raw hour values such as `24`, `168`, and `720`. | Added one range-label helper and reused `近 24 小时`, `近 7 天`, and `近 30 天` consistently; a regression test covers the rendered label contract. |

### Findings Deferred (With Tracking Issues)

None.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 2 |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE
<!-- REVIEW:END -->
