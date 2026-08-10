# Feature Specification: Toonflow and Paisio Video Routing

**Feature Branch**: `codex/issue-42-video-multi-upstream`

**Created**: 2026-08-10

**Status**: Draft

**Input**: Route Toonflow and Paisio through the relay as managed video upstreams, with authenticated capability discovery and auditable provider selection.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Use Both Eligible Video Upstreams (Priority: P1)

An operator enables Toonflow and Paisio as video upstreams. Comparable full and Fast video
requests are distributed across both healthy providers, while each downstream model name and
capability remains stable.

**Why this priority**: The current single-provider configuration creates a concentration risk and
prevents the operator from using the second paid upstream.

**Independent Test**: Submit a controlled sequence of equivalent full and Fast requests while both
providers are healthy and verify that both providers receive work without changing the downstream
request or response contract.

**Acceptance Scenarios**:

1. **Given** Toonflow and Paisio both advertise a requested full or Fast capability, **When** a
   controlled sequence of comparable requests is submitted, **Then** both providers receive tasks
   and no provider receives more than 60% of the first 100 eligible tasks.
2. **Given** only one provider advertises the requested capability, **When** a request is submitted,
   **Then** the request uses that provider and the reason is recorded.
3. **Given** a Mini request and only Toonflow has an approved Mini route, **When** the request is
   submitted, **Then** it uses Toonflow without attempting Paisio.

---

### User Story 2 - Fail Over Without Duplicate Generation (Priority: P2)

An operator can continue serving video requests when one upstream rejects a submission before
creating a task, without risking duplicate videos when the upstream task state is uncertain.

**Why this priority**: Availability is valuable only if fallback does not create double charges or
duplicate generations.

**Independent Test**: Simulate a definite pre-creation rejection and an ambiguous timeout. Verify
that the rejection falls back once, while the ambiguous timeout remains bound to the original
provider for reconciliation.

**Acceptance Scenarios**:

1. **Given** the selected provider definitively rejects a request before creating a task, **When** an
   alternate approved provider is healthy, **Then** the request is submitted once to the alternate.
2. **Given** submission may have created an upstream task, **When** the response is lost or times out,
   **Then** the system does not submit to another provider and reports a reconcilable state.
3. **Given** the gateway restarts after provider selection, **When** the job resumes, **Then** it uses
   the persisted provider and does not select a new one.

---

### User Story 3 - Audit Every Provider Choice (Priority: P3)

An operator can determine which upstream handled each task and why, while ordinary users see only
the stable model and task outcome.

**Why this priority**: Provider distribution and failover cannot be trusted or tuned without an
auditable decision record.

**Independent Test**: Inspect operational task records for normal selection, capability-only
selection, and safe fallback, then verify that public responses contain no internal provider name
or credential metadata.

**Acceptance Scenarios**:

1. **Given** any accepted task, **When** an operator inspects it, **Then** the record identifies the
   selected provider, stable model, resolution, catalog revision, selection reason, and fallback
   sequence.
2. **Given** an ordinary downstream response, **When** it is inspected, **Then** it exposes the stable
   contract and outcome but not internal provider names, credentials, or routing weights.

### Edge Cases

- Both providers are enabled but one has stale or incomplete capability data.
- A provider advertises a model name that has no reviewed stable mapping.
- Both providers become unavailable after a job has been accepted.
- An upstream returns an error after creating a task but before returning its task identifier.
- Repeated client requests reuse the same idempotency identifier.
- A request asks for audio, references, aspect ratio, duration, or resolution supported by only one route.
- The provider set or route weights change while existing jobs are running.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept video requests through one stable relay contract regardless of
  whether Toonflow or Paisio is selected.
- **FR-002**: The system MUST discover or verify each provider's authenticated model capabilities
  before making a route eligible.
- **FR-003**: Only reviewed mappings between provider model names and the fixed full, Fast, and Mini
  catalog MAY receive production traffic.
- **FR-004**: When both providers are eligible for a comparable full or Fast request, the system MUST
  distribute traffic across both using a deterministic, auditable policy.
- **FR-005**: Requests MUST be filtered by resolution, duration, aspect ratio, reference limits,
  operation mode, and audio support before provider selection.
- **FR-006**: Provider selection and its reason MUST be persisted before upstream submission.
- **FR-007**: A job MUST retain its selected provider and upstream model across polling, restart,
  delivery, and settlement.
- **FR-008**: Fallback MAY occur only after a definitive pre-creation failure; ambiguous submission
  outcomes MUST remain with the original provider for reconciliation.
- **FR-009**: Repeated requests with the same idempotency identity MUST NOT create more than one
  upstream generation.
- **FR-010**: Provider credentials MUST be read from server-managed secrets and MUST NOT appear in
  source control, logs, public responses, or monitoring output.
- **FR-011**: Public model names and capabilities MUST remain unchanged when provider availability or
  traffic distribution changes.
- **FR-012**: Operators MUST be able to disable either provider without invalidating jobs already
  assigned to it.

### Key Entities

- **Stable Video Model**: The fixed downstream model family and supported resolutions/capabilities.
- **Provider Capability**: A provider's authenticated, reviewed ability to serve a stable model,
  resolution, operation mode, and optional features.
- **Route Decision**: The persisted provider choice, selection reason, catalog revision, and ordered
  alternatives for one job.
- **Video Job**: The durable request, selected route, upstream task state, retries, and final outcome.
- **Provider Health**: Recent eligibility state used for new selections without changing existing jobs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With both providers healthy, each receives at least 40% of the first 100 comparable full
  or Fast requests.
- **SC-002**: In controlled failure tests, 100% of definitive pre-creation failures use at most one
  alternate submission and 100% of ambiguous outcomes create no alternate submission.
- **SC-003**: Replaying accepted requests and restarting the service creates zero duplicate upstream
  generations across the verification set.
- **SC-004**: Every accepted job has a complete provider-choice audit record, while zero public
  responses expose provider credentials or internal routing configuration.
- **SC-005**: A provider configuration change affects new jobs within one operational refresh cycle
  and leaves 100% of existing jobs bound to their original provider.

## Assumptions

- Toonflow and Paisio are the only providers in scope for this feature; Rolldek, PackAPI, and Unity2
  are not added to this routing policy.
- Full and Fast requests use an equal-share default when both providers are healthy and capable;
  future cost-based optimization is outside this feature.
- Mini uses only Toonflow unless authenticated Paisio capability evidence is later reviewed.
- The existing stable video names, official sale pricing, delivery behavior, and audio request field
  remain unchanged.
- Provider credentials already exist on the server and are not copied into the repository.
- CLR and unrelated NewAPI channels are outside scope.
