<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|---|---|
| Worker | `/root` |
| Issue | #186 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-10-07 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|---|---|---|
| 1 | Blindspots | FIXED | Definite no-task refusal, uncertain attempts, concurrent workers and frozen replay |
| 2 | Clarity | PASS | Explicit per-model mode/count/specification contracts |
| 3 | Maintainability | FIXED | Separate pure replay syntax from admission; release manifests and both Docker paths updated |
| 4 | Security | FIXED | Pinned-IP actual socket target, image identity/magic/bounds, local-only forced decoder, malformed URLs |
| 5 | Performance | FIXED | Bounded verification concurrency and overall deadline; close SQLite context resources |
| 6 | Documentation | PASS | Primary-contract boundaries and exact downstream tuples documented |
| 7 | Style | PASS | Existing JSON helpers and GORM transactional checks; relevant test suites and diff checks passed |

### Findings Fixed in This PR

1. Python HTTP base construction shadowed the IP-pinning method. Explicit instance binding and actual socket/TLS regression now enforce the intended target.
2. Operator probe budget/attempt registration was racy. Linux exclusive budget lock, exclusive attempt creation, durable fsync and decimal accounting precede paid POST.
3. Accepted image replay depended on live configuration. Pure identity comparison against frozen task payload now works after disabled/invalid contract or unavailable provider; changed content conflicts.
4. Definitive image refusal after preflight could strand a public hold. Only a first matching authenticated pre-creation refusal may zero-settle; earlier uncertain exposure remains unresolved rather than assumed free.
5. Concurrent workers could refund from a stale attempt counter. Transactional no-task settlement rechecks locked current counter/state/backend; admission checks update row count before POST. Deterministic interleaving regression covers it.
6. Generic Docker build omitted the new imported contract module. Both Docker copies and source-integrity coverage now include it.
7. Replay resolution normalization differed from first admission. New image syntax now requires canonical explicit resolution.
8. Image autodetection could process non-image bytes. Magic signatures, forced PNG/JPEG demuxers and local-only protocol whitelist precede codec/dimension acceptance. A disguised-playlist test ensures no decoder invocation. No nested-network exploit is claimed as reproduced.
9. Malformed image catalog fields could panic projection. Typed tuple matching skips only invalid image extensions while retaining ordinary catalog rows.
10. New provider platform48 action=generate receipts were not recognized. Strict model/platform/path identity now recognizes exact successful video bills, not generic image tasks.
11. Nested documented result formats were not delivered by the Nody-specific URL extractor. Original UUID is retained and nested video URL results are recognized.
12. Gateway SQLite context resources were not closed. Transaction completion now closes handles, with commit/reopen resource regression.
13. Live Grok Video 3 polling returned a scalar `output` HTTPS URL, which was classified as running. Exact scalar-output regression now succeeds, while explicit failed status and non-HTTPS results remain rejected.
14. Pinned download retries surrounded the context-manager yield, so caller I/O errors could produce a second yield. Retries now cover only connection setup; caller errors propagate once and resources close deterministically.

### Findings Deferred

None. Four models' undocumented multimodal modes and explicit first/last/video/audio inputs are deliberately unavailable, not claimed as implemented defects.

### Verification Boundary

- Relevant Python gateway suite:182 tests passing at this checkpoint.
- Go public-video and model suites passed; image-validation/debit, exact replay, no-task refund and concurrency interleaving regressions passed.
- Independent read-only security and seven-criterion behavioral reviewers reported no remaining code blocker after corrections.
- Six exact provider image tuples have successful authenticated task bills, totalCNY4.50. Further delivery/canary and controlled rollout verification are release gates and are NOT implied by code review completion.
- No production deployment, historical refund, price-schedule change or unrelated channel change is claimed here.

**Unaddressed: 0**
**Review Status: COMPLETE**
<!-- REVIEW:END -->
