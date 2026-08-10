# Tasks: Paisio-First Video Routing

**Input**: Design documents from `specs/047-paisio-priority-routing/`

## Phase 1: Specification and Baseline

- [x] T001 Record the fixed provider precedence and safety boundary.
- [x] T002 Inspect current catalog priority, route planning, persistence, and fallback behavior.
- [x] T003 Run the existing gateway suite as a clean baseline.

## Phase 2: User Story 1 - Prefer Paisio

- [x] T004 [US1] Write failing all-request-ID and checked-in catalog priority tests.
- [x] T005 [US1] Set shared Paisio routes before Toonflow and update catalog revision.
- [x] T006 [US1] Persist the fixed-priority selection reason and update gateway documentation.

## Phase 3: User Story 2 - Safe Fallback

- [x] T007 [US2] Verify definite Paisio rejection advances exactly once to Toonflow.
- [x] T008 [US2] Verify uncertain outcomes, task IDs, retries, and restarts do not cross providers.

## Phase 4: Review and Delivery

- [x] T009 Run Python compilation and the full gateway test suite.
- [x] T010 Complete code/security review with zero unaddressed findings.
- [x] T011 Raise and merge the reviewed PR.
- [x] T012 Back up, deploy, verify health, and run ten read-only production rounds.
