<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | `Codex root + runtime_repair_review` |
| Issue | #183 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-10-06 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | ✅ FIXED | 7 |
| 2 | Clarity | ✅ PASS | 0 |
| 3 | Maintainability | ✅ FIXED | 1 |
| 4 | Security | ⚠️ DEFERRED | 1 |
| 5 | Performance | ✅ FIXED | 1 |
| 6 | Documentation | ✅ PASS | 0 |
| 7 | Style | ✅ PASS | 0 |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | Modern route selector did not use capability/cooldown filter | DB/cache exclusions and modern + exact-production integration |
| 2 | Major | Parameter mismatch cooled down valid requests from other users | Request-only exclusions; capacity/key failures alone get scoped cooldown |
| 3 | Major | Error-body read exceptions left active gateway jobs | Protected error reading, close and terminal uncertain state |
| 4 | Major | Naked timeout and empty successful response looked definitely failed | Phase-based uncertainty, no replay |
| 5 | Minor | Claim and preparing phase had a restart gap | Atomic claim/phase transaction |
| 6 | Minor | Submission flag leaked into later pre-submit attempt errors | Per-attempt reset and protected submission-state header |
| 7 | Major | Definite pre-submit rejection could trip global uncertainty circuit | Native protected no-submit evidence and gateway regression |
| 8 | Major | Rollback failure reopened unsafe ingress | Only restore verified image/health/DB before releasing gates |
| 9 | Major | Mutable tags/source could pass stale canary guards | Source-manifest, app hashes and immutable image IDs frozen and compared |
| 10 | Major | Invalid Nginx candidate remained on disk | Restore/revalidate previous config on failed test/reload |
| 11 | Major | Post-release journal failure restarted accepted requests | Persist readiness before release; committed rollout does not unsafe-restart |

### Findings Deferred (With Tracking Issues)

| # | Severity | Finding | Tracking Issue | Justification |
|---|----------|---------|----------------|---------------|
| 1 | Major boundary review | Existing HTTPS reference fetch trusts redirects/address resolution | #184 | Caller validation and egress boundary not yet verified; no assertion of a demonstrated exploit; isolate follow-up from scoped deployment |

### Verification

- Complete local controller/dto/middleware/service/relay-channel/model suites passed.
- Local monitor tests: 195 passed with one Linux/fork-only skip; production relevant operational tests: 95 passed including that fork/deadline test.
- New gateway image: 9 tests passed.
- Latest exact production-source canary: ambiguous image submitted once with exact precharge refund; known quota rejection used backup; cached read/write normalized to exact 210 quota; unsupported Pro2K rejected before POST; Pro1K returned 1024x1024 in 23.27s and charged exactly 60000 quota = CNY0.12.
- GPT6.1 direct + native funded probes succeeded, exact base tariff billed 74 native quota; long context and cache boundaries tested without large paid requests.
- Private backups and ordinary existing-user public catalog verified; no wallet migration, recharge, Jojo revival or video configuration change.
- No secret in tracked files; `git diff --check` passed. Race was not executed locally (CGO compiler unavailable); ordinary suites and isolated server integrations passed.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 11 |
| Deferred (with tracking) | 1 |
| Unaddressed | 0 |

**Review Status:** ✅ COMPLETE
<!-- REVIEW:END -->
