# Research: Selective Upstream Updates

## Decision: Freeze the comparison instead of following a moving branch

**Decision**: Compare fork `main` at `f0fb7d0aa3da56f757fe23b4a2e461403fcf198a` against upstream
range `1721144221ec5c94dd87891a7ae1bee228e7bb63..2d8e50bf36e94200b809dfb39e73624ec48b1e23`.

**Rationale**: Git verifies the merge base and exactly 97 commits. A frozen range makes the ledger,
tests, and review reproducible even if upstream advances.

**Alternatives considered**: Tracking upstream `main` live was rejected because scope could change
during review. Comparing only releases was rejected because post-release security work would vanish.

## Decision: Do not merge or rebase the fork onto upstream

**Decision**: Review commits independently and port only selected changes.

**Rationale**: The fork is 32 commits ahead and 97 behind with customized routing, pricing,
monitoring, video settlement, stable model mappings, authentication, and operations. A bulk merge
would combine unrelated architecture and business-rule changes.

**Alternatives considered**: A full merge, rebase, or release replacement was rejected as
non-reversible and contrary to the user's conflict boundary.

## Decision: First batch contains OAuth callback proof and DOMPurify patch update

**Decision**:

- `e78e1db1e4ed7d65e37c2527826f290c0c63b041` is `manual-port`. Its pure test reproduces a login
  tab with a foreign opener being mistaken for an account-bind popup. The patch is clean against the
  fork and does not touch backend authentication, billing, or production data.
- `f250f3b589c836764954f646448084e93873798b` is `adopt`. It changes the direct dependency and
  override in `web/package.json` from DOMPurify 3.4.11 to 3.4.13 and applies cleanly. A frozen install
  correctly rejected the stale lock, after which Bun regenerated the matching `web/bun.lock`.

**Rationale**: Both changes are bounded, useful, reversible, and independently verifiable.

**Alternatives considered**: Adding every clean patch was rejected. Clean application does not prove
behavioral compatibility or adequate tests.

## Decision: Defer other clean frontend changes

**Decision**: Defer Turnstile reset (`ffeb1b24`), credential-autofill prevention (`2d8e50bf`), and
model-price preservation (`27235a27`).

**Rationale**: The first two lack an existing low-cost user-behavior harness in this fork. The price
change touches system-wide model pricing maps and has no upstream regression test, conflicting with
the fork's audited pricing safety rules.

**Alternatives considered**: Source-shape tests and untested adoption were rejected as weak evidence.

## Decision: Reject direct RelayKit and customized billing ports

**Decision**: RelayKit-dependent changes, tiered billing/refund concurrency changes, automatic
channel testing, and large route-editor refactors do not enter this batch.

**Rationale**: The fork does not contain upstream's `relaykit/` architecture and has independent
pricing, quota, refund, video, and channel-monitor invariants. Merge-tree simulation reports conflicts
for the highlighted Ali, Gemini, Claude, and Responses fixes.

**Alternatives considered**: Resolving conflicts opportunistically was rejected. Useful behaviors are
marked `manual-port` or `defer` for bounded future issues.

## Evidence

- `git merge-base origin/main upstream/main` returned the frozen merge base.
- `git rev-list --count <base>..upstream/main` returned 97.
- `git cherry -v origin/main upstream/main` found zero patch-equivalent commits.
- Three-way merge-tree checks identified the exact clean and conflicting candidates recorded above.
- No production endpoint, credential, database, or paid provider request was used in the audit.
