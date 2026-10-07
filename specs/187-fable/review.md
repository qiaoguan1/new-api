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
- 150 offline operations tests passed, including cancelled-attempt replay fencing, key promotion readback, proxy mounting, fresh upstream costs and durable rollout orchestration; independent implementation and security reviewers reported no remaining must-fix findings.
- Candidate sha256:7d231e67f87f98ace75943882f3b8128dadfd7963ff579b31d03a6999c30d2f2.
- Exact production backport: 11 changed/new files, 2042 unchanged baseline files verified byte-identical.
- Isolated candidate: 12 valid free full-path cases plus 5 pre-submit HTTP 400 cases; six retained-upstream real calls, all HTTP 200 and exact wallet/log charges.
- This artifact records source/canary review, not a claim of production enablement. Production rollout and eligible-user access are separate acceptance gates.
- Production attempt cancelled without any binary swap. Original compose/Nginx contents and the absence of DRAIN were verified; public site health remained successful and public Fable price rows remained zero. Host/container config inode mismatch was verified, with a read-only single-file bind. No proxy recreation, production Fable pricing write, provider-key promotion or customer mutation occurred.

### Separately Authorized Proxy Maintenance Review

The user selected A again, explicitly authorizing brief shared proxy maintenance.
Independent seven-criterion/security review passed for proxy_mount.py and its
31 tests. Compose changes are limited to two read-only nginx mounts. The main
include is pinned to default.conf only, so the dormant fangtang configuration
and historical files remain untouched and unloaded. Full nginx -T equality,
pinned image/environment/all networks, no-public-port canary and readiness
checks precede/verify the change. Only the exact proxy's restart policy is
temporarily suppressed; SIGQUIT drains accepted requests without forced kill.
Pending or ambiguous shutdowns retain owned DRAIN and evidence, never replay.
Native runtime and image gateway identities remain unchanged during this phase.
An isolated live check established environment values were identical but their
array order differed; semantic comparison now tolerates only that order, with
duplicate/malformed/missing variables rejected. The first attempt did not
touch live proxy settings. Its guarded reconciliation retains the started fence
until an exact unchanged-state archive receipt is durable. Five reconciliation
tests pass. The permanent pinned main config is stored under nginx/, not a
backup-retention directory.
Native/gateway mount signatures are persisted in JSON-compatible list form;
the same exact values survive private proof serialization and permission drift
still fails closed. A live read-only check confirmed the original container
IDs/start times/images/environment were unchanged before reconciliation.

Fresh retained-source cost checks use existing approved accounts and pricing
GETs only, require identical exact-model financial field coverage, effective
group ratios and authorized recharge conversion, and bind both source proofs
to the immutable build and True-plan. No price is staged after a failed check.
The durable finish helper executes each phase once, waits for existing price
and channel synchronizers, refreshes production key proof and audits public
ordinary/lian123 key access. No new generation task is submitted in this phase.
These are source reviews; live completion still requires separate evidence.

| Category | Count |
|---|---|
| Fixed in PR | All findings listed above |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE
Production acceptance passed on2026-10-08: immutable candidate, both enabled
routes, unchanged fresh source costs, exact private/public pricing,1620 eligible
keys, ordinary GET200 and lian1238/8 GET200,49 old channel configurations/all old
abilities/191 old metadata/full price maps preserved. No customer balances or
video configuration changed. Read-only audit scope was corrected to include
historical orphan rows; no DB records were deleted. completed-verified.json is
the durable final proof. Source PR remains separately tracked for human review.
<!-- REVIEW:END -->
