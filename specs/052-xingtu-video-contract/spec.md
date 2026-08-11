# Feature Specification: Unified XingTu Downstream Video Contract

**Feature Branch**: `codex/issue-52-xingtu-video-contract`  
**Created**: 2026-08-11  
**Status**: In Progress  
**Issue**: #52

## Goal

Publish and implement one versioned contract that XingTu AI software can deploy without knowing
which provider executes a video task. The same public fields must be returned at submit time, while
settlement is pending, and after final settlement.

## User Scenarios & Testing

### P1 - Submit once without duplicate provider work

XingTu submits a JSON request with its API token, the v2 contract header, and one stable idempotency
key. Repeating the same request returns the same public task; changing the payload under the same key
returns a conflict and never creates a second provider task.

### P1 - Observe reservation and exact final charge

The submit response exposes the frozen Ark-official x1.5 reservation as six-decimal CNY strings. A
successful task remains `settlement_pending` until trustworthy provider-task evidence arrives. The
final response exposes the user charge, refund, and supplement, while keeping provider cost private.

### P1 - Deliver the result only after settlement

The query response returns `result_delivery=pending_settlement` and `result=null` before final
settlement. Once settlement is final, it returns `result_delivery=ready` and a public result URL.

### P2 - Deploy from one complete guide

The downstream team receives one document listing required headers, fields, persistence rules,
polling, retries, errors, amount semantics, debt behavior, examples, and a go-live checklist.

## Requirements

- **FR-001**: `POST /v1/videos` MUST activate the unified contract only when
  `X-XingTu-Contract-Version: xtai-video-billing-v2` is present, preserving legacy clients.
- **FR-002**: A v2 request MUST supply bearer authentication, `Idempotency-Key`, matching
  `request_id`, `provider_id=video-aixingtu-api`, model, resolution, duration, prompt, aspect ratio,
  and an explicit `generate_audio` boolean.
- **FR-003**: Top-level `aspect_ratio` and `generate_audio` MUST be preserved and forwarded through
  provider metadata, including an explicit `false` value.
- **FR-004**: The server MUST create a durable idempotency claim before reservation or provider
  submission. Same-key/same-payload requests reuse the public task; same-key/different-payload
  requests return HTTP 409.
- **FR-005**: Public submit and query responses MUST use one DTO containing `id`, `request_id`,
  `status`, `result`, `result_delivery`, `billing`, and optional token `usage`.
- **FR-006**: All public CNY amounts MUST be decimal strings with exactly six fractional digits.
- **FR-007**: Task and billing state MUST remain independent. Provider success without evidence MUST
  return `succeeded + settlement_pending` and withhold the result.
- **FR-008**: Final public charge MUST equal the applied user charge (provider net cost x1.5), with
  explicit refund and supplement strings. It MUST NOT expose provider actual cost.
- **FR-009**: Public responses MUST NOT expose provider identity, raw upstream model/task IDs,
  credentials, route order, actual provider cost, margin, or evidence details.
- **FR-010**: Public errors MUST return a stable code, message, request ID when available, and a
  retryability flag. In-progress idempotent requests MUST not be resubmitted to another provider.
- **FR-011**: The deployment guide MUST define what the downstream supplies, stores, polls, displays,
  and treats as final, plus test vectors and a go-live checklist.
- **FR-012**: CLR and non-video billing MUST remain unchanged, and task persistence MUST remain
  compatible with SQLite, MySQL, and PostgreSQL.

## Edge Cases

- Missing or mismatched idempotency identifiers fail before any charge or provider request.
- A concurrent duplicate may receive `request_in_progress`; it waits and retries with the same key.
- A process crash after claiming a key never permits blind provider resubmission; the task becomes
  uncertain until recovered or reviewed.
- Explicit `generate_audio=false` is forwarded and never converted into an absent value.
- Failed tasks refund once; a repeated query does not cause another refund.
- `usage.total_tokens` is null when the provider does not report trustworthy token usage.
- A settled wallet task may return `settled_with_debt`; later submissions remain blocked until the
  wallet balance is positive.

## Success Criteria

- **SC-001**: Contract tests cover request alias forwarding, six-decimal output, state mapping,
  redaction, and idempotency replay/conflict.
- **SC-002**: Existing video billing, routing, provider-adapter, CLR, and non-video tests stay green.
- **SC-003**: The canonical guide and implementation examples serialize to the same field names.
- **SC-004**: Ten deterministic contract verification rounds pass without paid provider calls.

