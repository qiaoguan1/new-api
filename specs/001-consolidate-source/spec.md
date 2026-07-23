# Feature Specification: Consolidate Server Customizations

**Feature Branch**: `codex/issue-1-consolidate-server-source`

**Created**: 2026-07-23

**Status**: Draft

**Input**: Consolidate three divergent production-server source trees into one canonical,
traceable repository without deploying or changing the running service.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Existing Custom Behavior (Priority: P1)

As the service maintainer, I need every intentional customization currently present in the Git
working tree to remain available from a named branch so future work does not depend on an
uncommitted detached checkout.

**Why this priority**: Losing channel selection, monitoring, upstream-cost, or relay behavior
would change production semantics and make the current service impossible to reproduce.

**Independent Test**: Starting from the canonical branch alone, a reviewer can account for every
pre-consolidation working-tree change and validate the affected backend and frontend behaviors.

**Acceptance Scenarios**:

1. **Given** the original detached working tree and its verified backup, **When** the consolidated
   branch is compared with both, **Then** every intentional changed or untracked path is present
   or has a documented exclusion reason.
2. **Given** the consolidated source, **When** channel selection, channel monitoring,
   upstream-cost, and relay-focused checks run, **Then** their expected behavior passes.

---

### User Story 2 - Integrate the Final Payment Build (Priority: P2)

As the service maintainer, I need the final payment and channel-page behavior folded into the same
repository so the deployed feature set can be rebuilt without copying from a separate directory.

**Why this priority**: The deployed image includes later payment and channel-page changes that are
not fully represented in the Git working tree.

**Independent Test**: A reviewer can trace the final build's payment, routing, pricing, and user
interface differences into the consolidated branch and run focused payment and interface checks.

**Acceptance Scenarios**:

1. **Given** the final build copy, **When** its differences are reconciled with the canonical
   branch, **Then** payment initiation, payment status handling, configuration, routing, and the
   payment interface remain represented without discarding existing custom behavior.
2. **Given** a conflict between the candidate, final, and Git copies, **When** the decision is
   reviewed, **Then** the final build takes precedence for the payment feature while unrelated Git
   customizations are preserved.

---

### User Story 3 - Make Future Maintenance Reproducible (Priority: P3)

As a future maintainer, I need a clean repository, reconciliation record, and repeatable validation
guide so temporary source copies are no longer required for ordinary development.

**Why this priority**: Consolidation only remains valuable if another maintainer can understand,
test, and rebuild it safely.

**Independent Test**: A fresh checkout can follow the documented validation guide, obtain required
dependencies, and complete all selected checks without access to the candidate or final directory.

**Acceptance Scenarios**:

1. **Given** a fresh checkout of the feature branch, **When** the validation guide is followed,
   **Then** all required checks can be run without either source-copy directory.
2. **Given** the repository status, **When** tracked files are reviewed, **Then** no credentials,
   runtime data, dependency trees, generated build output, or ad-hoc backup files are included.

### Edge Cases

- A path exists only in the Git working tree and has no equivalent in the final copy.
- The final copy changes the same routing or interface file as the Git working tree.
- A backup-suffixed file resembles source but is not part of intended behavior.
- Dependency or generated files differ only because the copies were built at different times.
- A focused test requires credentials or a live payment provider and must use an isolated fixture.
- A validation command would rebuild or restart the production stack and must be rejected.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The consolidated branch MUST be the single authoritative location for all retained
  server customizations.
- **FR-002**: The system MUST preserve the detached working tree's intentional channel monitoring,
  channel selection, upstream-cost accounting, performance, and relay integration behavior.
- **FR-003**: The system MUST integrate the final copy's intended payment, payment configuration,
  routing, channel-page, and pricing behavior without overwriting unrelated retained changes.
- **FR-004**: Every path that differs among the Git, candidate, and final copies MUST be classified
  as retained, merged, generated, backup-only, or intentionally excluded.
- **FR-005**: Candidate-only behavior MUST NOT be retained when the final copy clearly supersedes it,
  unless an automated check or documented requirement proves it remains necessary.
- **FR-006**: Credentials, environment files, private keys, authentication material, runtime logs,
  database content, generated media, dependency trees, and build caches MUST remain untracked.
- **FR-007**: Existing project identity, attribution, metadata, and supported database behaviors
  MUST remain intact.
- **FR-008**: Focused automated checks MUST cover retained channel/relay behavior and integrated
  payment behavior before the consolidation is considered complete.
- **FR-009**: A repeatable validation guide MUST describe how to verify the canonical checkout from
  a clean dependency state.
- **FR-010**: The running production containers, images, databases, and routes MUST remain unchanged
  throughout this feature.

### Key Entities

- **Source Snapshot**: One of the Git working tree, candidate copy, final copy, or verified archive;
  identified by path, capture time, role, and integrity state.
- **Reconciliation Decision**: A path-level record containing the source versions, retained outcome,
  classification, rationale, and required verification.
- **Validation Result**: The command or scenario, expected outcome, observed outcome, and pass/fail
  status used to prove the consolidated repository is complete.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of differing paths among the three source trees have a recorded reconciliation
  outcome with no unexplained files.
- **SC-002**: 100% of selected channel, relay, payment, type, and build checks pass from the canonical
  branch without reading code from the candidate or final directory.
- **SC-003**: A fresh checkout can complete the documented validation workflow in one attempt without
  hidden server-specific source dependencies.
- **SC-004**: Repository scanning finds zero committed secrets, runtime data files, dependency trees,
  generated media, or ad-hoc backup files.
- **SC-005**: Production verification shows the same 12 running containers and successful public
  route responses before and after source consolidation.

## Assumptions

- The verified backup at `/root/maintenance-backups/20260723-142032` is the recovery baseline.
- The final copy supersedes the candidate copy for payment behavior and later channel-page changes.
- The Git working tree remains authoritative for its unrelated channel, upstream-cost, monitoring,
  and relay customizations.
- Generated dependencies can be restored from lockfiles and are outside the canonical source.
- Deployment and runtime migration are handled by a separate, explicitly approved issue.
