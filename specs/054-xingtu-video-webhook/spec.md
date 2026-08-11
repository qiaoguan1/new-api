# Feature Specification: Signed XingTu Video Webhooks

**Feature Branch**: `codex/issue-54-xingtu-video-webhook`  
**Created**: 2026-08-11  
**Issue**: #54

## Goal

Deliver durable, provider-neutral video status and billing events to the fixed XingTu downstream
endpoint so long-running video jobs do not require high-frequency polling.

## User Scenarios

### P1 - Receive task success promptly

When a v2 video first becomes successful, the same database transition durably creates one
`video.task.succeeded` event. The callback contains `settlement_pending` and no result URL.

### P1 - Receive final billing and result

When trustworthy actual-cost settlement completes, one `video.billing.settled` event contains the
canonical six-decimal charge/refund/supplement fields and the authenticated result URL.

### P1 - Recover from downstream outages

Callback delivery survives relay restarts, signs the exact stored payload, retries with bounded
backoff, and becomes dead-letter only after the documented attempt limit. Re-delivery uses the same
`event_id`; downstream processing is idempotent.

### P2 - Integrate from one protocol

The protocol separately lists what the downstream provides, what the relay provides, the request and
callback fields, signature verification, acknowledgement, retry, ordering, polling fallback, and
go-live checks.

## Functional Requirements

- **FR-001**: The callback URL and secret MUST be operator configuration, never per-request input.
- **FR-002**: Callback configuration MUST require HTTPS, a public DNS host, port 443, no userinfo or
  fragment, a 32+ byte secret, strict outbound timeout, no redirects, and public-IP DNS resolution.
- **FR-003**: Each callback event MUST be inserted durably and uniquely in the same transaction as the
  associated task/billing transition wherever that transition is transactional.
- **FR-004**: Event payload MUST contain `event_id`, `event_version`, `event_type`, `occurred_at`, and
  the canonical XingTu v2 response as `data`.
- **FR-005**: Public callbacks MUST NOT expose provider/channel names, upstream model/task IDs,
  provider cost/evidence, credentials, route order, or margin.
- **FR-006**: Signature MUST be lower-case hex HMAC-SHA256 over
  `<unix_timestamp>.<exact_raw_body>`, carried as `X-XingTu-Signature: v1=<hex>`.
- **FR-007**: Delivery MUST also include event ID, timestamp, delivery attempt, contract version, JSON
  content type, and a stable user agent.
- **FR-008**: Any HTTP 2xx acknowledges delivery. Network failures and non-2xx responses retry with
  the documented bounded schedule. Redirects MUST NOT be followed.
- **FR-009**: Concurrent workers MUST claim an event with database compare-and-swap semantics; a
  stale lease is recoverable after restart.
- **FR-010**: Events MUST include task success, final settlement (including debt), failed/refunded,
  payment-required, and pending-review states.
- **FR-011**: Callback delivery MUST NOT trigger provider requests, billing changes, or task state
  transitions.
- **FR-012**: Polling remains a low-frequency fallback; callback delivery is not required for legacy
  clients, CLR, or non-video tasks.

## Success Criteria

- Signature vectors, durable uniqueness, claim concurrency, retry/dead-letter, privacy, and all event
  transitions have automated tests.
- Existing video status, settlement, refund, idempotency, CLR, and non-video tests stay green.
- Ten deterministic webhook rounds pass without any paid provider call.

