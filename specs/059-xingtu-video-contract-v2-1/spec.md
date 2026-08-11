# Feature Specification: XingTu Video Contract v2.1

**Issue**: #59  
**Contract**: `xtai-video-billing-v2.1`

## Goal

Adopt the downstream-confirmed hard-quota contract without changing the existing Ark-official
reservation or trusted upstream settlement formulas. Keep explicit audio generation supported.

## Functional Requirements

- **FR-001**: A v2.1 video submission MUST reserve the complete Ark-official request cost multiplied
  by 1.5 before any provider side effect.
- **FR-002**: Wallet, subscription, and finite-token funding MUST fail atomically when the complete
  reservation is unavailable. No accepted v2.1 operation may create a negative balance.
- **FR-003**: A positive settlement supplement MUST be applied only when every hard quota source can
  fund it atomically. Otherwise the task MUST enter `payment_required`, retain the reservation, and
  withhold the result.
- **FR-004**: `settled_with_debt` MUST NOT be a deliverable v2.1 state. Legacy records with that state
  MUST be exposed as `payment_required` and MUST NOT release result content.
- **FR-005**: `generate_audio` MUST be an explicit boolean. Both `true` and `false` MUST be preserved;
  `true` MUST NOT be rejected or silently downgraded to an audio-disabled request.
- **FR-006**: Every XingTu-contract-tagged `POST /v1/videos` JSON request body MUST be limited to
  256 KiB before authentication distribution, reservation, or provider submission. Oversize
  requests return 413, including requests carrying an obsolete or unknown XingTu version.
- **FR-007**: The three stable model names and seven approved model-resolution combinations MUST
  remain unchanged.
- **FR-008**: Public CNY amounts MUST remain six-decimal strings. Final charge remains trusted
  provider net deduction multiplied by 1.5, with deterministic refund or supplement.
- **FR-009**: Existing v2 tasks MUST remain queryable and settle/refund safely during migration, but
  new contracted submissions MUST use v2.1.
- **FR-010**: Public responses, callbacks, and logs MUST NOT expose provider identity, upstream task
  IDs, raw cost evidence, credentials, or margin.

## Acceptance Scenarios

1. A wallet with one unit less than the reservation receives a stable quota error, retains its
   balance, and creates no task or provider request.
2. Two concurrent reservations cannot together drive one wallet below zero.
3. A successful task whose final charge exceeds the reservation remains `payment_required` when the
   supplement is unavailable and its content endpoint returns 402.
4. A legacy `settled_with_debt` task returns `billing.status=payment_required`, never `ready`.
5. `generate_audio=true` survives validation, fingerprinting, routing, and provider adaptation.
6. A 262,145-byte XingTu-tagged request is rejected with 413 before request parsing; a 262,144-byte
   body is allowed to continue. Ordinary requests without a XingTu contract header keep their
   existing limits.
7. Existing official pricing and actual-cost settlement tests remain unchanged and green.

## Out of Scope

- CLR and non-video pricing.
- Removing an audio track already present in provider output.
- Embedding large Base64 media in the JSON contract; downstream uses HTTPS references.
