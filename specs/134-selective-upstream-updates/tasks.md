# Tasks: Selective Upstream Updates

**Input**: Design documents from `specs/134-selective-upstream-updates/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Behavioral upstream changes require RED-GREEN regression evidence before implementation.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish reproducible upstream and Spec Kit state.

- [x] T001 Verify the frozen Git range and 97-commit count using `specs/134-selective-upstream-updates/quickstart.md`
- [x] T002 Verify the issue branch, project status, and Spec Kit governance in `.specify/memory/constitution.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Complete the audit and safety gates before modifying runtime code.

- [x] T003 [P] Record all 97 immutable commit dispositions in `specs/134-selective-upstream-updates/upstream-compatibility.md`
- [x] T004 [P] Record merge-tree, dependency, architecture, and safety decisions in `specs/134-selective-upstream-updates/research.md`
- [x] T005 Validate ledger uniqueness, range membership, and dispositions against `specs/134-selective-upstream-updates/contracts/compatibility-ledger.md`

**Checkpoint**: No runtime code changes before the complete ledger passes.

---

## Phase 3: User Story 1 - Complete Compatibility Decision (Priority: P1) 🎯 MVP

**Goal**: Give the owner a complete and reproducible disposition for every frozen upstream commit.

**Independent Test**: The quickstart ledger command reports 97 unique in-range commits and no gaps.

- [x] T006 [US1] Run and record the ledger coverage validation from `specs/134-selective-upstream-updates/quickstart.md`

**Checkpoint**: The audit is independently useful even before any code adoption.

---

## Phase 4: User Story 2 - Safe First Update Batch (Priority: P2)

**Goal**: Deliver the OAuth callback proof fix and DOMPurify patch without changing protected behavior.

**Independent Test**: The OAuth regression fails before its implementation, passes afterward, and all
frontend checks pass with DOMPurify 3.4.13.

### Tests for User Story 2 ⚠️

- [x] T007 [US2] Add the upstream foreign-opener OAuth regression in `web/src/features/auth/lib/__tests__/oauth-callback-mode.test.ts`
- [x] T008 [US2] Run the focused OAuth test before implementation and record RED evidence in `specs/134-selective-upstream-updates/verification.md`

### Implementation for User Story 2

- [x] T009 [US2] Manually port the callback proof logic from upstream `e78e1db1e4ed` into `web/src/features/auth/lib/oauth-callback-mode.ts`
- [x] T010 [US2] Stamp same-origin bind popups in `web/src/features/profile/components/tabs/account-bindings-tab.tsx`
- [x] T011 [US2] Resolve login versus bind callbacks by positive proof in `web/src/routes/oauth/$provider.tsx`
- [x] T012 [US2] Run the focused OAuth regression and record GREEN evidence in `specs/134-selective-upstream-updates/verification.md`
- [x] T013 [US2] Adopt upstream DOMPurify patch `f250f3b589c8` in `web/package.json` and regenerate `web/bun.lock`
- [x] T014 [US2] Run Bun tests, typecheck, changed-file lint, and production build from `web/package.json`

**Checkpoint**: The first update batch is independently revertible and frontend-clean.

---

## Phase 5: User Story 3 - Future Upgrade Continuity (Priority: P3)

**Goal**: Preserve enough provenance and validation detail for later bounded upgrade batches.

**Independent Test**: A maintainer can identify selected, equivalent, deferred, and rejected commits
and reproduce all no-production validation from checked-in artifacts.

- [x] T015 [US3] Finalize first-batch provenance and unchanged exclusions in `specs/134-selective-upstream-updates/upstream-compatibility.md`
- [x] T016 [US3] Run affected Go and custom operations regression suites from `specs/134-selective-upstream-updates/quickstart.md`

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T017 Re-run Spec Kit prerequisite, checklist, and quickstart validation for `specs/134-selective-upstream-updates/`
- [x] T018 Complete comprehensive review and post the review artifact to `https://github.com/qiaoguan1/new-api/issues/134`
- [x] T019 Address every review finding and rerun all affected tests documented in `specs/134-selective-upstream-updates/verification.md`
- [ ] T020 Commit, push, create the PR from `.github/PULL_REQUEST_TEMPLATE.md`, monitor CI, merge, and set Project 1 status to Done

---

## Dependencies & Execution Order

- Setup tasks T001-T002 precede the foundational audit.
- T003 and T004 can proceed independently; T005 depends on both.
- User Story 1 depends on T005.
- User Story 2 depends on User Story 1; T007-T008 MUST precede T009-T011.
- T012 depends on T009-T011; T014 depends on T012-T013.
- User Story 3 depends on the completed first batch and documentation.
- Review and delivery depend on all selected user stories and current test evidence.

## Implementation Strategy

The audit is the MVP. The first code batch remains intentionally small: one tested OAuth behavior and
one dependency patch. Every other upstream change remains unchanged and traceable for a future issue.
