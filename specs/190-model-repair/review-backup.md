<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | root with recovery_backup_fix_193 and independent model_callability_fix_191 review |
| Issue | #193; parent #190 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-10-10 Asia/Shanghai |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | FIXED | Required SQLite names, WAL data, directory drift and destination scope tested |
| 2 | Clarity | PASS | Configured coverage distinguished from full-host and cross-store atomic recovery |
| 3 | Maintainability | PASS | Explicit root/runtime allowlists; legacy CLI defaults retained |
| 4 | Security | FIXED | No links/traversal/special files; private root-owned archives and descriptors |
| 5 | Performance | FIXED | Bounded time/bytes/files/disk; hash and archive phases enforce deadline |
| 6 | Documentation | FIXED | Image-layer and off-host gaps, source ownership and restore rehearsal documented |
| 7 | Style | PASS | stdlib only; 28 tests and private-path checks pass |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | Runtime task DBs, secrets/config, upload and adapter state omitted | Explicit verified roots with fresh fixed-container descriptors |
| 2 | Major | Raw SQLite copy could miss committed WAL | Online backup plus quick_check and content proof |
| 3 | Major | Hash/manifest verification could exceed total deadline | Budget passed into each block and manifest phase; RED/GREEN test |
| 4 | Major | Destination could chmod disk root/stack ancestor | Guard before directory creation/chmod; regression test |
| 5 | Minor | Empty/non-root runtime directory restoration ambiguous | Private original UID/GID/mode and empty-directory metadata |

### Findings Deferred (With Tracking Issues)

None of this bounded recovery-scope change. Full-host image layers/off-host copy
and globally atomic cross-store recovery are expressly not claimed.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 5 |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE. Root reran all 28 backup/installer tests successfully.
Production installation and non-destructive restore checks are separate acceptance.
<!-- REVIEW:END -->
