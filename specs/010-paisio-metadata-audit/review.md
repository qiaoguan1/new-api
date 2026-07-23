<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | `/root` |
| Issue | #10 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-07-23T21:54:40+08:00 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | FIXED | 4 |
| 2 | Clarity | PASS | 0 |
| 3 | Maintainability | FIXED | 2 |
| 4 | Security | FIXED | 1 |
| 5 | Performance | PASS | 0 |
| 6 | Documentation | PASS | 0 |
| 7 | Style | FIXED | 1 |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | Missing account metadata could fall back to the legacy paid generation probe. | All daily availability checks now call only the read-only model catalog; no runtime fallback remains. |
| 2 | Major | A base URL already ending in `/v1` produced `/v1/v1/models`. | The patch normalizes root and `/v1` base URLs and verifies both through mocked production-copy execution. |
| 3 | Minor | The deployment patch's retained compatibility anchor caused a false idempotence failure. | Full replacement detection now takes precedence; apply/check/reapply and hash equality are tested. |
| 4 | Major | An upstream error string could theoretically echo an account secret into the ledger. | Metadata errors are sanitized with both username and password redaction, with an integration-style regression test. |
| 5 | Minor | `enable_groups` assumed a list even though upstreams may return comma-separated text. | Group metadata is normalized through the existing parser and both representations are tested. |
| 6 | Major | Four authenticated accounts were falsely rejected because upstream internal pricing-group labels did not match the login group name. | Authenticated `/api/user/models` visibility is now authoritative; pricing availability and regression tests use that account-scoped result. |
| 7 | Major | Topaz channel type 58 does not implement OpenAI `/v1/models`, so the generic read-only probe returned 404. | Type 58 now uses Topaz's free authenticated `GET /video/status` catalog and never creates an upscale job. |
| 8 | Minor | A second deployment of the improved metadata function could append a duplicate definition to the first deployed version. | The patcher now upgrades the existing function through AST-bounded replacement and rejects unknown implementations. |

### Findings Deferred (With Tracking Issues)

None.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 8 |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE
<!-- REVIEW:END -->
