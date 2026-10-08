# Tasks: Nody multimodal video relay

**Input**: [spec.md](spec.md), [plan.md](plan.md)
**Tests**: mandatory per repository and implementation workflow.

## Phase 1: Setup and Research

- [x] T001 Create issue186 and verify project1 In Progress; create clean scoped branch.
- [x] T002 Read installed Spec Kit spec/plan/tasks templates and record feature scope.
- [x] T003 Read the provider-owned API attachment and record exact new mode/price evidence.
- [x] T004 Confirm current deploy paths, safe media source and budget accounting without generating tasks.

## Phase 2: User Story 1 - Image/First-frame Request

- [x] T005 [US1] Add failing exact public/gateway/Nody image request and legacy-text regression tests.
- [x] T006 [US1] Implement confirmed public whitelist, normalization and adapter fields.
- [x] T007 [US1] Verify asset failure before reservation/submission, stable idempotency and role ordering.

## Phase 3: User Story 2 - Additional Confirmed Modes

- [x] T008 [US2] Add reference tests and explicit unsupported first/last rejection; no primary first/last contract exists for this batch.
- [x] T009 [US2] Implement per-model role/count/spec constraints and safe media verification.
- [x] T010 [US2] Verify reserve/final actual-cost settlement and pending/uncertain no-replay behavior.

## Phase 4: User Story 3 - Discovery and Rollout

- [x] T011 [US3] Run relevant regressions and isolated-canary contracts.
- [x] T012 [US3] Real lowest-spec validation within CNY50 cumulative exposure; record task/cost/result evidence.
- [x] T013 [US3] Synchronize only verified capability/price/marketplace rows and downstream examples.
- [x] T014 Comprehensive/security review and resolve findings before release.
- [x] T015 Private backup, controlled rollout and public/legacy verification.
- [x] T016 Remove unnecessary task-owned files, commit/PR with attached artifact, and report partial gates honestly.

## Dependencies

T003-T004 gate implementation. Contract tests precede their implementation. Unknown provider fields/costs cannot be enabled by assumption. T012 and T014 gate production advertising and rollout.
