# Feature Specification: Selective Upstream Updates

**Feature Branch**: `codex/issue-134-selective-upstream`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Review the open-source relay framework updates, adopt what is usable,
and leave conflicting or inapplicable changes unchanged."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Obtain a Complete Compatibility Decision (Priority: P1)

As the relay owner, I can see a complete, evidence-backed disposition for every upstream change in
the frozen range so that no update is silently omitted or adopted by assumption.

**Why this priority**: A complete audit is the safety gate for every later code change.

**Independent Test**: Compare the immutable upstream commit list with the audit and verify that each
commit appears exactly once with a valid disposition, rationale, and affected area.

**Acceptance Scenarios**:

1. **Given** the frozen upstream range, **When** the audit is validated, **Then** all 97 commits have
   exactly one recorded disposition.
2. **Given** an upstream change overlapping a customized production area, **When** it is reviewed,
   **Then** the audit identifies the protected behavior and does not classify it for direct adoption.

---

### User Story 2 - Receive a Safe First Update Batch (Priority: P2)

As the relay owner, I receive a first batch containing only compatible, useful upstream corrections
without changing customized routing, pricing, monitoring, video settlement, or stable model mapping.

**Why this priority**: It realizes value from the audit while limiting the blast radius.

**Independent Test**: Revert the batch as one unit, reapply it, and run its regression tests plus all
affected subsystem tests; both the baseline and upgraded behavior remain deterministic.

**Acceptance Scenarios**:

1. **Given** a compatible upstream correction with observable value, **When** it is adopted, **Then**
   a regression test demonstrates the corrected behavior and related custom tests still pass.
2. **Given** a conflicting, redundant, or unverifiable upstream change, **When** the batch is built,
   **Then** the current customized implementation remains unchanged and the reason is documented.

---

### User Story 3 - Continue Future Batches Safely (Priority: P3)

As a future maintainer, I can continue deferred compatible work from the audit without repeating the
entire investigation or confusing intentional rejections with unfinished work.

**Why this priority**: The upstream project will continue changing after this batch.

**Independent Test**: A maintainer can identify the upstream range, selected batch, deferred items,
conflicts, validation commands, and rollback boundary using only checked-in upgrade artifacts.

**Acceptance Scenarios**:

1. **Given** a later upgrade cycle, **When** a maintainer reads the ledger, **Then** they can determine
   which commits were adopted, manually ported, already equivalent, deferred, or rejected.

### Edge Cases

- An upstream commit contains both compatible and conflicting hunks.
- A commit is absent from ancestry but its behavior was independently implemented in the fork.
- Upstream tests assume data, routes, providers, or UI architecture replaced by fork customizations.
- A useful fix touches billing, authentication, migrations, or secrets and requires broader review.
- Two upstream commits depend on each other but only one is independently compatible.
- The upstream branch changes after the audit range is frozen.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The upgrade MUST freeze the upstream range from merge base
  `1721144221ec5c94dd87891a7ae1bee228e7bb63` through
  `2d8e50bf36e94200b809dfb39e73624ec48b1e23` and record its 97 commits.
- **FR-002**: Every commit in the frozen range MUST appear exactly once in the compatibility ledger.
- **FR-003**: Every ledger entry MUST use one disposition: `adopt`, `manual-port`,
  `already-equivalent`, `defer`, or `reject-with-conflict`.
- **FR-004**: Every disposition MUST record rationale, affected subsystem, and immutable provenance.
- **FR-005**: Changes overlapping customized routing, pricing, channel monitoring, video settlement,
  stable model mappings, authentication, or production operations MUST receive explicit overlap review.
- **FR-006**: A behavioral upstream change MAY be adopted only when a meaningful regression test
  fails before or is demonstrably absent before the implementation and passes afterward.
- **FR-007**: The first batch MUST be independently revertible and MUST contain only changes whose
  dependencies and affected tests are understood.
- **FR-008**: Deferred, conflicting, redundant, and unverifiable items MUST leave current production
  behavior unchanged.
- **FR-009**: Relevant backend, frontend, database, relay, billing, routing, and custom operations
  suites MUST pass for every affected area.
- **FR-010**: The work MUST NOT deploy production, submit paid upstream requests, access credentials,
  or write production databases.
- **FR-011**: The upgrade artifacts MUST document validation and rollback instructions for the batch.

### Key Entities

- **Upstream Range**: Immutable merge base, head commit, commit count, and source repository.
- **Compatibility Entry**: One upstream commit, affected subsystem, disposition, rationale, overlap,
  dependency, test evidence, and adopted commit reference when applicable.
- **Upgrade Batch**: Ordered compatible changes, validation evidence, and rollback boundary.
- **Protected Invariant**: Existing customized behavior that an upstream change must not overwrite.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the 97 frozen upstream commits have exactly one valid compatibility entry.
- **SC-002**: 100% of adopted behavioral changes have regression evidence and passing affected suites.
- **SC-003**: Zero protected customized invariants change without an explicit separate authorization.
- **SC-004**: The first update batch can be reverted and reapplied without manual data repair.
- **SC-005**: Zero production mutations, paid requests, credential reads, or secret disclosures occur.
- **SC-006**: A future maintainer can identify all remaining deferred and rejected work from the ledger
  without comparing the original 97 commits again.

## Assumptions

- The upstream range remains frozen even if upstream advances during implementation.
- Fork `main` at `f0fb7d0aa3da56f757fe23b4a2e461403fcf198a` is the upgrade baseline.
- Missing ancestry does not prove missing behavior; equivalent custom implementations are valid.
- High-risk compatible changes may be deferred to child issues instead of entering the first batch.
- Production deployment requires a separate explicit request and is outside Issue #134.

## Safety Boundaries *(mandatory for upgrades and migrations)*

- Preserve XingTu routing, pricing, monitoring, video, model mapping, security, and public contracts.
- Do not bulk-merge or rebase onto upstream `main`.
- Do not read production secrets, copy production data, submit paid tasks, or deploy.
- The complete first batch must be revertible as a repository change without production data repair.

## Change Disposition *(mandatory for upstream adoption)*

The immutable comparison range is
`1721144221ec5c94dd87891a7ae1bee228e7bb63..2d8e50bf36e94200b809dfb39e73624ec48b1e23`.
Each of its 97 commits receives exactly one of these dispositions:

- `adopt`: compatible commit can be applied with its provenance and tests.
- `manual-port`: useful behavior is compatible, but only selected logic can safely enter the fork.
- `already-equivalent`: the fork independently provides the same observable behavior.
- `defer`: potentially useful but requires a separate bounded issue or missing evidence.
- `reject-with-conflict`: incompatible with a protected invariant or current architecture.
