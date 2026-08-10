# Feature Specification: Paisio-First Video Routing

**Feature Branch**: `codex/issue-47-paisio-priority-routing`

**Created**: 2026-08-10

**Status**: Draft

**Input**: Prefer Paisio for video submission and use Toonflow only after a safe, definite pre-creation failure.

## User Scenarios & Testing

### User Story 1 - Prefer Paisio for Shared Capabilities (Priority: P1)

An operator wants every new full or Fast request supported by both providers to submit to Paisio
first, while preserving the stable downstream model contract.

**Why this priority**: Deterministic provider precedence is the requested operating policy.

**Independent Test**: Resolve many request IDs for each shared model/resolution and prove every
persisted plan begins with Paisio and lists Toonflow second.

**Acceptance Scenarios**:

1. **Given** both providers are healthy and support the request, **When** a route plan is created,
   **Then** Paisio is first and Toonflow is second regardless of request ID or catalog order.
2. **Given** only Toonflow supports a capability, **When** a route plan is created, **Then** Toonflow
   remains the only candidate.

### User Story 2 - Fall Back Without Duplicate Generation (Priority: P2)

An operator wants a definite Paisio pre-creation rejection to use Toonflow while ambiguous outcomes
remain bound to Paisio for reconciliation.

**Why this priority**: Availability must not create duplicate videos or duplicate upstream charges.

**Independent Test**: Fake adapters prove one definite rejection advances once and a timeout,
missing response, existing task ID, retry, restart, or poll failure never creates a second task.

**Acceptance Scenarios**:

1. **Given** Paisio definitively rejects before creating a task, **When** Toonflow is the next
   persisted candidate, **Then** the gateway submits once to Toonflow.
2. **Given** the Paisio outcome is uncertain or contains a task ID, **When** handling the failure,
   **Then** the gateway does not submit to Toonflow.

### Edge Cases

- Paisio is unhealthy or disabled before a new plan is created.
- Paisio is removed after an existing job has already persisted its route plan.
- Only one route remains after request-specific aspect-ratio or reference filtering.
- A repeated request ID arrives after a safe fallback or service restart.

## Requirements

### Functional Requirements

- **FR-001**: Shared full and Fast capabilities MUST persist Paisio before Toonflow.
- **FR-002**: Route order MUST be independent of request ID and catalog input order.
- **FR-003**: Capability-only routes MUST remain unchanged.
- **FR-004**: Fallback MAY occur only for a definite pre-creation failure without a task ID.
- **FR-005**: Uncertain submission outcomes MUST NOT advance to another provider.
- **FR-006**: Existing jobs MUST retain their persisted provider and route plan across retries and restarts.
- **FR-007**: Selection reason and fallback history MUST remain auditable in private state.
- **FR-008**: Public responses MUST NOT expose provider order or routing metadata.
- **FR-009**: Pricing, monitoring, credentials, and CLR MUST remain unchanged.

### Key Entities

- **Route Priority**: Lower integer priority is attempted before a higher integer priority.
- **Persisted Route Plan**: Immutable ordered provider candidates saved before submission.
- **Safe Fallback Event**: One atomic move to the next candidate after a definite pre-creation failure.

## Success Criteria

### Measurable Outcomes

- **SC-001**: 100% of tested shared-capability plans begin with Paisio and list Toonflow second.
- **SC-002**: 100% of definite pre-creation failure tests advance at most once.
- **SC-003**: 100% of uncertain-outcome and idempotency tests create zero alternate submissions.
- **SC-004**: Ten production read-only verification rounds report zero duplicate request IDs.

## Assumptions

- Paisio and Toonflow remain the only providers in this policy.
- Mini remains Toonflow-only until separately reviewed capability evidence exists.
- Existing safe fallback and SQLite persistence are reused rather than replaced.
- Provider pricing is not a routing input.
