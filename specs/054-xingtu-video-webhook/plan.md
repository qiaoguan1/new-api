# Implementation Plan: Signed XingTu Video Webhooks

## Technical Context

- Go, Gin, GORM; SQLite/MySQL/PostgreSQL compatibility.
- Existing `xtai-video-billing-v2` task and settlement state.
- One operator-configured XingTu downstream endpoint.

## Design

1. Add a `xingtu_video_webhook_events` outbox table with deterministic event IDs and unique transition
   keys, stored canonical JSON payload, lease/retry state, and redacted failure metadata.
2. Add transactional enqueue helpers used by task success CAS, settlement, payment-required, and
   refund transitions.
3. Add a master-node worker that claims due events, signs the stored bytes, posts through a strict
   HTTPS public-IP-only client, and records delivered/retry/dead-letter state.
4. Start delivery only when a complete valid callback configuration exists; events remain durable
   while configuration is absent.
5. Update the canonical downstream protocol. Polling remains recovery-only.

## Security Boundaries

- No per-request URL, redirects, HTTP scheme, IP literals, private/link-local/multicast DNS results,
  proxy inheritance, response-body logging, or secret persistence.
- Exact raw body is signed; downstream verifies timestamp freshness and MAC in constant time.
- Callback payload reuses the already-redacted public DTO.

## Retry Contract

Initial delivery is immediate. Failure delays are 10s, 30s, 1m, 2m, 5m, 10m, 30m, then hourly.
At 30 total attempts the event enters `dead_letter`. The same `event_id` and payload are reused.

