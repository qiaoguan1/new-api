# Feature Specification: Audited Video Multi-Channel Routing and Safe Auth Refresh

**Feature Branch**: `codex/issue-63-video-multichannel-auth-refresh`  
**Created**: 2026-08-12  
**Status**: In Progress  
**Input**: Enable every approved video provider and keep provider billing authorization available without bypassing human verification.

## User Scenarios & Testing

### User Story 1 - Use every safe video provider (Priority: P1)

As the relay operator, I want every configured video provider that can both generate and prove exact task-level cost to participate in the production route, so one provider outage does not stop all video traffic.

**Independent Test**: Load a reviewed catalog with multiple eligible providers, simulate a definite rejection on the first provider, and prove the same durable request advances once to the next provider without creating duplicate upstream tasks.

**Acceptance Scenarios**:

1. **Given** several reviewed providers with valid generation and billing credentials, **when** a supported request is submitted, **then** the gateway persists the complete deterministic provider plan before the first upstream call.
2. **Given** the first provider definitely rejects before returning an upstream task ID, **when** fallback runs, **then** the gateway uses the next approved provider once.
3. **Given** a provider has no exact task-level billing collector, **when** production eligibility is calculated, **then** that provider is excluded from new v2.1 jobs.

---

### User Story 2 - Keep renewable authorization current (Priority: P1)

As the relay operator, I want renewable provider authorization refreshed before expiry so successful videos do not become stuck waiting for settlement.

**Independent Test**: Supply a near-expiry renewable token and a fake refresh endpoint; prove an atomic refresh replaces it, preserves 0600 permissions, and a failed refresh leaves the still-valid token untouched.

**Acceptance Scenarios**:

1. **Given** a refreshable credential within the refresh window, **when** the scheduled refresh runs, **then** a new credential is validated and atomically installed.
2. **Given** a refresh attempt fails, **when** the existing credential remains valid, **then** it is preserved and the failure is alerted without logging secrets.
3. **Given** an expired credential, **when** route eligibility is evaluated, **then** new jobs cannot use that provider while historical query, settlement and Webhook processing remain available.

---

### User Story 3 - Handle CAPTCHA-bound authorization safely (Priority: P1)

As the relay operator, I want advance warnings and a safe replacement procedure for credentials that require human verification, rather than an unsafe automation that bypasses CAPTCHA.

**Independent Test**: Supply a CAPTCHA-bound token expiring inside configured warning windows; prove one deduplicated warning per threshold and verify that a replacement token is validated before atomic installation.

**Acceptance Scenarios**:

1. **Given** a CAPTCHA-bound token, **when** it approaches expiry, **then** the operator receives deduplicated advance warnings.
2. **Given** a replacement token, **when** the operator installs it, **then** its format, expiry and provider identity are checked before replacement.
3. **Given** no replacement arrives before expiry, **when** the token expires, **then** the affected provider fails closed for new jobs and no CAPTCHA bypass is attempted.

### Edge Cases

- An API response is ambiguous, lacks a unique provider task ID, or returns more than one billing record.
- A submission times out after the provider may have accepted it.
- A refresh response is valid JSON but contains a token for another audience/provider.
- The refreshed token has a shorter lifetime than the current token.
- The credential file is group/world-readable, a symlink, oversized, or replaced concurrently.
- One provider can generate a model/resolution but cannot return exact cost for that same route.
- A route is removed while existing jobs are running.
- PackAPI or Unity2 appears in a discovered catalog or credential file.

## Requirements

### Functional Requirements

- **FR-001**: The production provider universe MUST be restricted to reviewed gateway adapters and MUST exclude PackAPI and Unity2.
- **FR-002**: A provider MUST be eligible for a new v2.1 job only when generation credentials, catalog capability, health and an exact task-level billing collector are all ready.
- **FR-003**: Route order MUST be deterministic and persisted before submission; replaying a `request_id` MUST reuse the persisted plan.
- **FR-004**: Cross-provider fallback MUST occur only after a definite pre-acceptance rejection without an upstream task ID; uncertain outcomes MUST fail closed for reconciliation.
- **FR-005**: Exact settlement MUST require one unique terminal provider billing record matched to the persisted provider task ID.
- **FR-006**: Refreshable credentials MUST refresh before expiry through provider-approved server APIs and be atomically installed only after validation.
- **FR-007**: Failed refresh MUST preserve a still-valid credential and generate a sanitized alert.
- **FR-008**: CAPTCHA-bound credentials MUST NOT be refreshed through CAPTCHA bypass; the system MUST provide advance expiry warnings and a validated, atomic operator replacement path.
- **FR-009**: Credential and state files MUST be regular non-symlink files with mode 0600; secrets MUST be absent from logs, public responses, Webhooks, repository files and test snapshots.
- **FR-010**: Expired/unavailable provider authorization MUST remove only that provider from new routing; historical job query, settlement retry and Webhook delivery MUST continue.
- **FR-011**: Refresh and eligibility checks MUST be idempotent, concurrency-safe and restart-persistent.
- **FR-012**: The operator MUST receive provider-neutral status showing readiness, expiry class and last refresh result without credential values.

### Key Entities

- **Provider Eligibility**: Reviewed provider ID, catalog capability, generation readiness, billing readiness, health and exclusion reason.
- **Credential Lease**: Provider, credential kind, expiry, refresh capability, refresh window and safe storage path; never includes secret material in public output.
- **Refresh State**: Last attempt, last success, sanitized failure code and deduplication thresholds.
- **Route Plan**: Immutable ordered provider/model candidates persisted for a request.
- **Settlement Evidence**: Unique terminal provider-task record with exact cost, observation time, fingerprint and revision.

## Success Criteria

- **SC-001**: Every reviewed provider with complete generation and exact-billing capability is visible as eligible in production; incomplete providers are excluded with a safe reason.
- **SC-002**: Deterministic tests prove no duplicate upstream submission across replay and fallback cases.
- **SC-003**: Refresh tests prove atomic replacement, 0600 permissions, failure preservation and secret-free output.
- **SC-004**: CAPTCHA-bound expiry tests prove warnings and fail-closed behavior without automated CAPTCHA bypass.
- **SC-005**: The full gateway and channel-monitor suites pass, followed by ten read-only production rounds with zero paid generation calls.

## Assumptions

- “All channels” means every server-configured, reviewed video provider supported by the gateway, not unrelated text/image providers.
- Static API keys do not require periodic mutation; their readiness is checked periodically and failures are alerted.
- Provider APIs may not offer a refresh token. Human-verification-bound tokens use scheduled warning and operator replacement rather than simulated login.
- Provider/channel identities and cost evidence remain private to the relay.

