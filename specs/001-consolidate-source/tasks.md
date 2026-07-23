# Tasks: Consolidate Server Source

**Input**: Design documents in `/specs/001-consolidate-source/`
**Tests**: Required by the project constitution and Issue #1 acceptance criteria.

## Phase 1: Setup and Baseline

- [x] T001 Confirm branch, remotes, current worktree inventory, and production container baseline
- [x] T002 Create `specs/001-consolidate-source/reconciliation-manifest.md` with every reviewed final delta and its disposition
- [x] T003 Confirm the verified maintenance backup contains the current canonical tree before removing any redundant artifact

## Phase 2: Test-First WeChat Pay Foundation

- [x] T004 [P] Import `setting/payment_wechat_test.go` from the final comparison tree
- [x] T005 [P] Import `model/topup_wechat_test.go` from the final comparison tree
- [x] T006 [P] Import `controller/topup_wechat_test.go` from the final comparison tree
- [x] T007 Run the focused tests before implementation and record the expected failing evidence

## Phase 3: Backend Implementation

- [x] T008 Integrate `setting/payment_wechat.go` and the reviewed payment-setting compatibility change
- [x] T009 Integrate WeChat top-up model behavior and idempotency changes in `model/topup.go`
- [x] T010 Integrate `controller/topup_wechat.go` and reviewed changes in `controller/topup.go`
- [x] T011 Integrate WeChat routes plus channel slash/no-slash compatibility in `router/api-router.go`
- [x] T012 Integrate the WeChat SDK dependency changes in `go.mod` and `go.sum`
- [x] T013 Correct test certificate/public-key fixtures while preserving strict runtime validation
- [x] T014 Run focused backend tests and fix all feature-related failures

## Phase 4: Frontend and Build Compatibility

- [x] T015 Integrate the wallet WeChat payment dialog, polling hook, exports, API, types, and payment utilities
- [x] T016 Integrate the reviewed channel API normalization and generated route-tree update
- [x] T017 Integrate the reviewed completion-ratio override in `setting/ratio_setting/model_ratio.go`
- [x] T018 Integrate `.dockerignore` node-module exclusions without adding backup artifacts

## Phase 5: Repository Consolidation

- [x] T019 Remove only the explicitly inventoried redundant backup artifacts from the canonical worktree after backup verification
- [x] T020 Verify candidate/final remain comparison-only and no source path references them as a runtime dependency
- [x] T021 Run `gofmt` on integrated Go files and `git diff --check`

## Phase 6: Validation

- [x] T022 Run the full Go test suite (with a temporary untracked classic embed placeholder; removed after validation)
- [x] T023 Restore frontend dependencies and run default type-check/build; classic build remains resource-blocked by SIGKILL
- [x] T024 Scan the change set for credentials, private keys, debug artifacts, and unintended generated files
- [x] T025 Confirm all production containers and health routes are unchanged and running

## Phase 7: Review and Delivery

- [ ] T026 Perform comprehensive code review and payment/security review; resolve actionable findings
- [ ] T027 Post implementation and validation evidence to GitHub Issue #1
- [ ] T028 Commit the consolidated change set with Issue #1 linkage and push the feature branch
- [ ] T029 Open a pull request linked to Issue #1 and verify CI status

## Dependencies

- T001–T003 precede every source change.
- T004–T006 can run in parallel; T007 must follow them and precede T008–T013.
- T014 depends on T008–T013.
- T015–T018 depend on a green focused backend baseline from T014.
- T019–T021 follow implementation; T022–T025 follow repository consolidation.
- T026 follows all validation; T027–T029 follow successful review.

## Implementation Notes

- Preserve all pre-existing canonical customizations outside explicitly reviewed paths.
- Copy no `.bak`, `.backup-*`, private key, certificate, `.env`, or runtime data artifact.
- A failing test in T007 is required evidence of the red phase, not permission to weaken production validation.
- Production deployment and service restart are explicitly out of scope.
