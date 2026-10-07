# Feature Specification: Nody multimodal video relay

**Feature Branch**: `codex/issue-186-nody-multimodal`
**Created**: 2026-10-07
**Status**: Research
**Input**: User requests relay-side modification to support verified image, first/last-frame and reference-input modes for the seven connected Nody models.
**Tracking**: https://github.com/qiaoguan1/new-api/issues/186

## User Scenarios & Testing

### User Story 1 - Verified image-to-video (Priority: P1)

A valid existing personal key submits a supported image/first-frame request, obtains one task and receives the finished video with correct settlement.

**Independent Test**: Mock transport regression followed by the lowest confirmed real specification within the approved budget.

**Acceptance Scenarios**:
1. Given verified upstream parameters and price, a valid image request reaches the provider with its actual required field names.
2. Invalid, inaccessible or unsupported assets fail before any paid provider submission or wallet reservation.
3. Legacy text requests remain compatible and have unchanged pricing and task isolation.

### User Story 2 - Explicit frame/reference semantics (Priority: P2)

A client distinguishes first frame, first/last frames and reference assets without guessing from the number of images.

**Independent Test**: Exact upstream request assertions for each evidenced model/mode and illegal role/count rejection.

**Acceptance Scenarios**:
1. First/last roles retain order and cannot be silently converted to multi-reference mode.
2. Missing roles, unsupported modes, unknown fields and excessive asset sizes/counts fail closed.

### User Story 3 - Truthful public discovery (Priority: P3)

Clients see only validated supported modes/specifications in capabilities, prices and the model square, with examples matching the deployed request contract.

**Independent Test**: Compare catalog, quote, submit and downstream documentation for each enabled tuple.

### Edge Cases

- Signed URL expiry/rotation, untrusted/private addresses, redirects, wrong media type/content identity.
- Concurrent duplicate requests and changed-content reuse of the same idempotency key.
- Submit timeout after possible task creation, delayed billing, terminal generation failure and result-delivery failure.
- Provider supports a subset of advertised model modes or prices depend on input type.

## Requirements

- **FR-001**: Provider evidence defines each model/mode/specification; unverified modes remain unavailable.
- **FR-002**: Public entry, gateway validation and adapter use explicit compatible role semantics.
- **FR-003**: Bounded safe media verification and stable content identity precede paid submission.
- **FR-004**: New reserve quotes use verified cost evidence; final cost remains actual upstream cost times 1.5.
- **FR-005**: Public discovery is updated only after exact request, asynchronous result and billing validation.

### Key Entities

- Verified model-mode contract: provider endpoint, accepted asset fields/roles, limits, output specifications and price evidence.
- Asset identity: role, ordinal, content digest, source and bounded format/size metadata.
- Video task: existing authenticated task, stable idempotency identity, reservation, upstream identity and final cost evidence.

## Success Criteria

- **SC-001**: Every newly enabled model/mode has a passing contract test and successful paid result/billing proof.
- **SC-002**: Invalid asset/role cases create zero upstream tasks and zero wallet mutations.
- **SC-003**: Duplicate and uncertain submissions never create a second provider task.
- **SC-004**: Existing text-video regression suite remains passing; unrelated routes and balances remain unchanged.

## Assumptions and Boundaries

- Reuse current personal-key authentication and v2.2 billing/task endpoints.
- Seven models only: wan3.0-video, wan3.0-video-prime, flux-3-video, grok-video-3, grok-imagine-1.5-video, grok-imagine-video-official, omni-flash.
- User approved at most CNY50 for this round of paid tests, no recharge. Reserve pending/uncertain test exposure against the cap.
- No historical refunds, unrelated channel changes, pricing-schedule changes or replay of unresolved tasks.
- No assumption that each model supports every input mode. Provider contract/price gates are explicit.
