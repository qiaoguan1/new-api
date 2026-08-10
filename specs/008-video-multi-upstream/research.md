# Research: Toonflow and Paisio Video Routing

## Decision: Keep the dedicated durable video gateway

**Rationale**: Production video requests already use a durable job gateway with provider adapters,
bounded media delivery, polling, and a SQLite WAL state machine. Moving asynchronous jobs back into
NewAPI would discard working idempotency and delivery protections.

**Alternatives considered**:

- NewAPI channel load balancing: rejected because `/internal/xtai-video-jobs/` deliberately bypasses
  NewAPI's synchronous distributor and its task records do not represent these jobs.
- A new external scheduler: rejected as unnecessary operational complexity.

## Decision: Deterministic rendezvous ordering by request identity

**Rationale**: Hash-ranked eligible routes provide a stable equal-share distribution without a
mutable global counter. The same request identity yields the same first choice, including after a
restart, while the full ordered plan can be persisted for audit and safe fallback.

**Alternatives considered**:

- Static priority: rejected because it always selects one provider.
- In-memory round robin: rejected because restarts and multiple workers change the sequence.
- Lowest-cost routing: deferred because issue #43 must first establish trustworthy comparable cost.

## Decision: Persist an ordered route plan before submission

**Rationale**: Existing jobs persist one provider but cannot explain alternatives or safely change
provider. Storing all approved candidates, the current index, selection reason, and history makes
recovery deterministic and audit complete.

**Alternatives considered**:

- Recompute on retry: rejected because catalog or health changes could cause a duplicate submission
  to a new provider.
- Store only a provider name: rejected because it cannot prove why fallback was allowed.

## Decision: Fallback only on definitive pre-creation failure

**Rationale**: HTTP responses or provider payloads that definitively reject before returning a task
identifier are safe to advance. Transport failures, timeouts, invalid success responses, and any
response containing a task identifier are uncertain and remain with the original provider.

**Alternatives considered**:

- Fall back on every retryable error: rejected because 5xx/timeouts can occur after task creation.
- Never fall back: safe but does not meet the availability requirement.

## Decision: Persisted provider health derived from recent submissions

**Rationale**: A provider with three consecutive definitive pre-creation failures in a five-minute
window is excluded from new selections for a bounded cooldown. Running/succeeded submissions reset
the sequence. Existing jobs are never moved because of health.

**Alternatives considered**:

- External health endpoint only: insufficient because an endpoint can be healthy while submissions fail.
- Permanent disable on one failure: too sensitive and operationally disruptive.

## Verified production facts

- Current environment enables only Toonflow even though adapters for both providers exist.
- Current catalog revision routes all seven stable combinations only to Toonflow.
- Paisio previously completed production `sd2-720p` jobs and its authenticated catalog advertises
  the reviewed `sd2` full/Fast resolution names.
- Toonflow supports the three stable families used by the current catalog.
- Existing public job snapshots omit provider identity; this behavior is retained.
