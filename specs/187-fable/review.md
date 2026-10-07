<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|---|---|
| Issue | #187 |
| Scope | MAJOR, exact Fable 5.1 only |
| Security-Sensitive | YES |
| Reviewed | 2026-10-07 |

### Criteria Results

| # | Criterion | Status | Findings |
|---|---|---|---|
| 1 | Blindspots | FIXED | Unsupported protocols, ambiguous request fields and incomplete deployment recovery were addressed. |
| 2 | Clarity | PASS | Named model-scoped helpers and explicit verification phases. |
| 3 | Maintainability | PASS | Shared non-Fable behavior retained; deployment is independently testable. |
| 4 | Security | FIXED | Reject conflicting/duplicate/noncanonical tariff fields before billing; preserve key/IP/wallet boundaries; bind image/source/plan proofs. |
| 5 | Performance | PASS | No added upstream request; bounded maintenance/drain; other services are not recreated. |
| 6 | Documentation | PASS | Spec, plan, tasks, API scope, tariff vectors and operations evidence documented. |
| 7 | Style | PASS | Go formatting, typed DTOs, Python checks and diff checks passed. |

### Findings Fixed in This PR

- Fable accepts only the two verified Chat/Messages protocols, not Responses or compaction.
- Original request and forwarded effort fields cannot disagree; adaptive thinking preserves native output_config.
- Scoped validation errors return HTTP 400, with other model status behavior unchanged.
- Aggregate cache creation and explicit 5m/1h tokens are accounted independently without double counting.
- Deployment restores ingress safely on partial gate installation, tracks only its own DRAIN inode, and never restores customer wallets.
- Maintenance is the first matching text/image regex; video/callback and model-catalog locations remain unchanged.
- Historical uncertain image jobs do not block drain forever; unknown states still fail closed.
- Ready runtime is not restarted after ingress/audit failure; all proof and command artifacts are bound or uniquely named.
- Finalizer independently rechecks all price vectors, exact image, isolated database fingerprint, proof bytes and both plan hashes.
- A post-canary deployment finding was fixed: atomic host replacement is not compatible with a single-file Nginx bind. Preflight now rejects it before writes, and reload checks actual container file contents. Shared proxy remount/recreation remains a separately authorized operational action, not an automatic code workaround.

### Verification

- Full relevant Go suites passed in the workspace and exact production-baseline image build.
- 94 offline operations tests passed, including cancelled-attempt replay fencing, key promotion readback and mount safety; independent implementation and security reviewers reported no remaining must-fix findings.
- Candidate sha256:7d231e67f87f98ace75943882f3b8128dadfd7963ff579b31d03a6999c30d2f2.
- Exact production backport: 11 changed/new files, 2042 unchanged baseline files verified byte-identical.
- Isolated candidate: 12 valid free full-path cases plus 5 pre-submit HTTP 400 cases; six retained-upstream real calls, all HTTP 200 and exact wallet/log charges.
- This artifact records source/canary review, not a claim of production enablement. Production rollout and eligible-user access are separate acceptance gates.
- Production attempt cancelled without any binary swap. Original compose/Nginx contents and the absence of DRAIN were verified; public site health remained successful and public Fable price rows remained zero. Host/container config inode mismatch was verified, with a read-only single-file bind. No proxy recreation, production Fable pricing write, provider-key promotion or customer mutation occurred.

| Category | Count |
|---|---|
| Fixed in PR | All findings listed above |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE
<!-- REVIEW:END -->
