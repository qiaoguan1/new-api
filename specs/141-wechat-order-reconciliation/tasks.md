# Tasks: 当前用户微信订单状态同步

## Phase 1: Evidence and Setup

- [x] T001 Record sanitized production evidence on Issue #230 and create the Project #4 item
- [x] T002 Create the isolated new-api worktree, branch and Spec Kit artifacts under `specs/141-wechat-order-reconciliation/`

## Phase 2: Test-First Contracts

- [ ] T003 Add failing eligibility and max-five budget tests in `controller/topup_wechat_test.go`
- [ ] T004 Add failing SUCCESS, CLOSED and non-terminal mapping tests in `controller/topup_wechat_test.go`
- [ ] T005 Add failing deadline/error fail-open tests in `controller/topup_wechat_test.go`
- [ ] T006 Add failing current-user list integration test in `controller/topup_test.go`

## Phase 3: Implementation

- [ ] T007 [US1] Define the minimal WeChat order query interface and bounded reconciliation helper in `controller/topup_wechat.go`
- [ ] T008 [US1] Reuse existing validation, idempotent credit and pending-status transition paths in `controller/topup_wechat.go`
- [ ] T009 [US2] Apply max-five/shared-deadline/fail-open behavior in `controller/topup_wechat.go`
- [ ] T010 [US3] Invoke reconciliation only for authenticated current-user list results in `controller/topup.go`

## Phase 4: Verification and Delivery

- [ ] T011 Run focused controller/model tests and full `go test ./...`
- [ ] T012 Complete production read-only state-count verification without exposing order IDs
- [ ] T013 Post comprehensive/security review artifact to Issue #230 and resolve every finding
- [ ] T014 Commit, push, pass new-api CI, merge, update Project and separately decide production deployment

## Dependencies

- T003–T006 must fail before T007–T010.
- T007 defines the seam required for all deterministic query tests.
- T008 precedes T009 so safety invariants remain authoritative before batching.
- T010 is last to expose the behavior through the list endpoint.
- No implementation tasks are parallel because they share the payment reconciliation contract.
