# Tasks: Toonflow and Paisio Video Routing

**Input**: Design documents from `specs/008-video-multi-upstream/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Behavioral tests are mandatory and must fail before implementation.

## Phase 1: Setup

- [x] T001 Import the exact credential-free production gateway source into `ops/video-job-gateway/`
- [x] T002 [P] Document ownership, local tests, deployment, and rollback in `ops/video-job-gateway/README.md`
- [x] T003 [P] Add secret-safe defaults for both providers in `ops/video-job-gateway/env.example` and `ops/video-job-gateway/compose.yaml`

---

## Phase 2: Foundational Safety

- [x] T004 Write failing legacy-schema migration tests in `ops/video-job-gateway/tests/test_store_routes.py`
- [x] T005 [P] Write failing deterministic route-order tests in `ops/video-job-gateway/tests/test_routing.py`
- [x] T006 [P] Write failing public-redaction and idempotency tests in `ops/video-job-gateway/tests/test_gateway_routing.py`
- [x] T007 Add provider route-plan entities and validators in `ops/video-job-gateway/routing.py`

**Checkpoint**: Route decisions have a tested standalone contract before gateway integration.

---

## Phase 3: User Story 1 - Use Both Eligible Video Upstreams (Priority: P1) 🎯 MVP

**Goal**: Full and Fast traffic is deterministically shared between eligible Toonflow and Paisio routes; Mini remains capability-only.

**Independent Test**: Resolve 100 controlled full/Fast requests and prove both providers receive 40-60 requests, while Mini resolves only to Toonflow.

### Tests for User Story 1

- [x] T008 [US1] Extend failing catalog capability and equal-share tests in `ops/video-job-gateway/tests/test_routing.py`
- [x] T009 [P] [US1] Add failing request-constraint filtering tests in `ops/video-job-gateway/tests/test_gateway_routing.py`

### Implementation for User Story 1

- [x] T010 [US1] Return all eligible resolution routes from `ops/video-job-gateway/catalog.py`
- [x] T011 [US1] Select a deterministic ordered route plan in `ops/video-job-gateway/app.py`
- [x] T012 [US1] Add reviewed Paisio full/Fast routes while retaining Toonflow full/Fast/Mini routes in `ops/video-job-gateway/catalog.json`

**Checkpoint**: Both providers receive eligible new jobs without changing the public model contract.

---

## Phase 4: User Story 2 - Fail Over Without Duplicate Generation (Priority: P2)

**Goal**: Definite pre-creation failures advance once; uncertain outcomes never cross providers.

**Independent Test**: Fake adapters prove a definite rejection uses the second persisted candidate and a timeout creates no second submission, including after restart.

### Tests for User Story 2

- [x] T013 [US2] Add failing atomic route-advance and restart tests in `ops/video-job-gateway/tests/test_store_routes.py`
- [x] T014 [P] [US2] Add failing definite-versus-uncertain fallback tests in `ops/video-job-gateway/tests/test_gateway_routing.py`

### Implementation for User Story 2

- [x] T015 [US2] Add additive route-plan columns and legacy migration in `ops/video-job-gateway/store.py`
- [x] T016 [US2] Implement atomic pre-creation route advance and history in `ops/video-job-gateway/store.py`
- [x] T017 [US2] Integrate safe fallback and persisted recovery in `ops/video-job-gateway/app.py`
- [x] T018 [US2] Exclude recently unhealthy providers from new plans without moving existing jobs in `ops/video-job-gateway/store.py` and `ops/video-job-gateway/app.py`

**Checkpoint**: Failover improves availability with zero duplicate upstream submissions.

---

## Phase 5: User Story 3 - Audit Every Provider Choice (Priority: P3)

**Goal**: Operators can audit provider decisions while downstream responses remain provider-neutral.

**Independent Test**: Internal rows contain the route plan, reason, revision, and history; public snapshots contain none of those fields.

### Tests for User Story 3

- [x] T019 [US3] Add failing audit completeness and public-redaction tests in `ops/video-job-gateway/tests/test_gateway_routing.py`

### Implementation for User Story 3

- [x] T020 [US3] Persist bounded selection reasons and fallback history in `ops/video-job-gateway/store.py`
- [x] T021 [US3] Expose aggregate provider health only on the authenticated operational endpoint in `ops/video-job-gateway/app.py`
- [x] T022 [US3] Document the internal route-decision contract in `ops/video-job-gateway/README.md`

**Checkpoint**: Every accepted task is auditable without leaking internal routing publicly.

---

## Phase 6: Verification and Production Delivery

- [x] T023 Run the full gateway suite and source compilation described in `specs/008-video-multi-upstream/quickstart.md`
- [x] T024 Perform credential, URL-host, duplicate-submit, and public-data security review for `ops/video-job-gateway/`
- [x] T025 Post the complete review artifact with zero unaddressed findings to GitHub issue #42
- [x] T026 Build and verify a dark production image against a copied SQLite database
- [x] T027 Back up production source, catalog, environment, image, and SQLite state before activation
- [x] T028 Enable Toonflow and Paisio, deploy the reviewed image, and run bounded real canaries
- [x] T029 Run ten read-only verification rounds for distribution, idempotency, health, and public redaction
- [x] T030 Record production evidence, rollback paths, and acceptance results in GitHub issue #42

---

## Dependencies & Execution Order

- Setup tasks T001-T003 precede all code work.
- Foundational tests T004-T006 must fail before T007 and story implementation.
- User Story 1 is the routing MVP and precedes fallback integration.
- User Story 2 depends on the persisted route plan created for User Story 1.
- User Story 3 depends on the same persisted metadata but is independently testable for redaction.
- Production delivery begins only after all tests and review gates pass.

## Parallel Opportunities

- T002 and T003 can run in parallel with the credential-free source import.
- T005 and T006 touch independent test files.
- T009 can run while catalog selection work begins after its failing test exists.
- T014 can run independently from store migration test implementation.

## Implementation Strategy

1. Import and freeze the deployed source as the baseline.
2. Build the pure deterministic route-plan module and prove equal sharing.
3. Integrate eligible route resolution and dual-provider catalog entries.
4. Add durable schema migration and safe fallback.
5. Prove audit completeness and public redaction.
6. Review, dark-canary, back up, activate, and verify production.
