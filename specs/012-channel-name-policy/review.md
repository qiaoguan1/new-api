<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | `/root` |
| Issue | #12 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-07-23T22:22:27+08:00 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | FIXED | 2 |
| 2 | Clarity | PASS | 0 |
| 3 | Maintainability | FIXED | 2 |
| 4 | Security | PASS | 0 |
| 5 | Performance | PASS | 0 |
| 6 | Documentation | PASS | 0 |
| 7 | Style | PASS | 0 |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | A partial or stale inventory could rename the wrong operational channel. | The policy explicitly maps all 35 IDs and rejects missing, extra, or manually drifted rows before and again inside the locked transaction. |
| 2 | Major | A name migration could accidentally alter status, keys, models, or routing fields. | SQL updates only `channels.name` and compares an all-other-fields JSONB fingerprint inside the transaction before commit. |
| 3 | Major | Updating the already-deployed standalone guide could duplicate the setup block. | The patcher now recognizes and upgrades the deployed v1 guide, and apply/check/reapply idempotence is tested on the production HTML copy. |
| 4 | Minor | Two backups started in the same second could use the same filename. | Backup timestamps now include microseconds and files remain atomic mode-0600 writes. |
| 5 | Minor | The fingerprint temporary table relied on connection teardown for cleanup. | It is explicitly declared `ON COMMIT DROP`. |

### Findings Deferred (With Tracking Issues)

None.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 5 |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE
<!-- REVIEW:END -->
