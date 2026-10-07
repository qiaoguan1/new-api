<!-- REVIEW:START -->
## Code Review — supported media expansion

| Property | Value |
|----------|-------|
| Issue | #186 / PR188 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-10-08 |

### Criteria results

| # | Criterion | Status | Review focus |
|---|-----------|--------|--------------|
| 1 | Blindspots | FIXED | Per-model count/role/spec limits; empty Wan prompt; empty legacy Grok AV arrays; definitive no-task rejection vs uncertain task preservation |
| 2 | Clarity | PASS | Separate candidate wire mapping from evidenced production admission and legacy text pricing |
| 3 | Maintainability | FIXED | Shared AV validator policies retain Seedance defaults; both image and media profiles coexist |
| 4 | Security | FIXED | HTTPS443/public-DNS pinning; hash/MIME/decoder proof; offline selected-stream probes; no private billing fields in public projection |
| 5 | Performance | FIXED | Shared60s mixed-media deadline, bounded reads/bytes, decoder timeout and fixed selected-stream output |
| 6 | Documentation | FIXED | Explicit code-only/not-deployed status; acceptance evidence and current funded testing boundary recorded |
| 7 | Style | PASS | Pure typed Python modules, Go require/assert tests, source-integrity manifest and both Docker COPY lists updated |

### Findings fixed

1. Candidate wire URL validation accepted non443 ports: require443 before any provider call.
2. File/link assets could be silently ignored: explicitly reject these unverified input forms.
3. Audio input duration was optional in exact profiles: require and bind measured audio seconds whenever audio is present.
4. Generic duration tolerance could cross exact billing boundaries: Nody uses microsecond-level measured matching; Seedance retains its existing tolerance.
5. Images and AV had separate time budgets: one60s deadline now covers all Nody mixed material verification.
6. Decoder output enumerated every track: select one declared-type track, fixed fields, suppress stderr, bound accepted stdout, reject malformed/nonfinite/out-of-range duration.
7. Go public normalization had generic Seedance limits: use Nody model-local limits including Wan audio-only and1s references; do not relax Seedance.
8. Empty AV arrays redirected legacy Grok requests into a new path: normalize empties before classification; preserve accepted old fingerprints/quotes.
9. Wider Grok profiles could use the older minimal image quote: only original six tuples retain that path; new tuples bind the full media preflight contract.
10. New media modules were absent from release Docker COPY/source integrity: include both and test tampering rejection.
11. Replay test could start a recovery thread before its mock was installed: patch submit across the fixture lifecycle; no live-provider credentials or paid test were used.
12. Grok Imagine1.5 legacy image routing incorrectly reused its480p text tuple: use explicit720p/6s image tuples; verify original fingerprints, preflight and frozen replay for all six deployed image cases.
13. Empty Wan media-only prompts were rejected at the public boundary: allow this only for Wan media requests; retain legacy text/other model prompt requirements.
14. New media retail bounds did not match the private cost ceiling: allow exact media retail up toCNY150 from maximumCNY100 evidenced cost, retain original text/image limits, and test the quota boundary.
15. Marketplace metadata omitted AV roles/formats/limits: preserve known consumer fields through a retail-profile-matched whitelist; do not expose stale/unpriced combinations or private evidence.

### Verification and limits

Parent verification: full Python gateway regression257 tests passed, Go public-video/model tests passed, Go vet passed, Linuxamd64 public-video build passed (CGO disabled), and git diff checks passed. Build binary SHA-256: `1c8e02e88a6cebec6062422a548bd40aa6cb1f4c860079bc891d0441dce60b0e`. No missing CI check is claimed green.

Wan's new mixed-reference direct upstream case has a unique original task, authenticated actual billing, measured input fixtures and bounded MP4 delivery proof. Other new branches do not have live acceptance evidence yet. No production deployment, customer/key ACL mutation, unrelated pricing/channel change, recharge or uncertain replay occurred in this expansion.

**Unaddressed:** 0 for reviewed implementation findings; remaining external live-acceptance/rollout gates are tracked in expansion.md.

**Review Status:** COMPLETE for code; production acceptance is partial and deployment remains pending.
<!-- REVIEW:END -->
