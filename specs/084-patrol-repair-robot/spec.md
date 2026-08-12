# Feature Specification: Daily Patrol and Safe Self-Healing Robot

**Feature Branch**: `codex/issue-85-patrol-repair-bot`  
**Created**: 2026-08-13  
**Status**: In Progress  
**Input**: Inspect the relay every day, repair bounded operational faults automatically, and notify the administrator when safe repair is impossible.

## User Scenarios & Testing

### User Story 1 - Detect operational failures before users report them (Priority: P0)

As the relay operator, I want one daily patrol to inspect the complete production chain so stale collection, pricing, settlement, Webhook, backup, service and capacity failures are visible with evidence.

**Independent Test**: Run the patrol against a fake host snapshot containing healthy and unhealthy components and verify every configured check produces a deterministic, sanitized result.

### User Story 2 - Repair only proven-safe failures (Priority: P0)

As the relay operator, I want reversible and idempotent failures repaired automatically without permitting the robot to invent prices, balances, credentials or settlement evidence.

**Independent Test**: Simulate an allowlisted stopped stateless service and a forbidden pricing/settlement fault; prove only the service receives one bounded repair and both outcomes are verified and audited.

### User Story 3 - Escalate unresolved failures once (Priority: P0)

As the relay operator, I want unresolved incidents emailed through the existing NewAPI notification transport with deduplication and a recovery notice.

**Independent Test**: Repeat the same failure across runs, advance beyond the reminder interval, then recover it; prove notification counts are one open, one reminder and one recovery.

### User Story 4 - Operate the robot without exposing root or secrets (Priority: P1)

As the relay operator, I want a loopback-only status/run API and a Beijing-time systemd timer, while the network process remains unprivileged and root repairs run only through a constrained oneshot.

**Independent Test**: Verify non-loopback binding and missing/incorrect bearer credentials fail, duplicate triggers coalesce, the timer persists missed runs, and the API never returns secret values.

## Functional Requirements

- **FR-001**: Every run MUST use an explicit policy file and produce an atomic private JSON report containing check evidence, proposed actions, executed actions and unresolved incidents.
- **FR-002**: Checks MUST cover systemd and Docker health, the public status endpoint, disk capacity, backup freshness, upstream collection, text/image pricing, official video pricing, balance monitoring, video settlement backlog and Webhook backlog.
- **FR-003**: Automatic actions MUST be restricted to a reviewed allowlist of reversible, idempotent commands with per-action cooldowns, maximum attempts and a per-run action budget.
- **FR-004**: The robot MUST NOT create paid tasks, invent or write price/cost/balance/settlement evidence, mutate CLR, bypass CAPTCHA, rotate provider credentials, prune Docker storage or alter database rows.
- **FR-005**: Repairs MUST be followed by the same health check; an unsuccessful repair MUST become unresolved and MUST NOT loop within the same run.
- **FR-006**: Incident state MUST survive restarts, deduplicate repeated failures and emit an explicit recovery event.
- **FR-007**: Notifications MUST reuse the existing RootAuth-protected NewAPI mail endpoint and MUST NOT store SMTP credentials or accept arbitrary recipients/content from callers.
- **FR-008**: Reports, logs and API responses MUST redact tokens, passwords, authorization headers, query credentials and private upstream identifiers.
- **FR-009**: The control API MUST listen only on a loopback address, require a server-generated bearer token from a regular 0600 file and expose only health, sanitized status and run-trigger operations.
- **FR-010**: The API process MUST remain unprivileged; privileged actions MUST run in a separate root oneshot triggered through a narrow filesystem boundary.
- **FR-011**: Daily execution MUST use a persistent systemd timer in `Asia/Shanghai`, plus flock-based concurrency prevention for timer, API and manual runs.
- **FR-012**: Unknown, malformed or incomplete evidence MUST fail closed and be reported as unknown, never as healthy or zero.

## Success Criteria

- **SC-001**: Unit tests cover every check class, allowed/forbidden action, repair verification, cooldown, action budget, notification lifecycle, redaction and API authentication.
- **SC-002**: A production dry run and one live bounded run complete without paid generation, database mutation, pricing mutation or credential mutation.
- **SC-003**: Ten read-only production rounds show healthy core services, no duplicate repairs, valid report/state permissions and no secret exposure.
- **SC-004**: An injected disposable failure is repaired or escalated exactly once and its recovery is recorded.

## Assumptions

- The existing NewAPI root notification endpoint and administrator mailbox remain the authoritative email transport.
- Pricing and settlement scripts already implement their own fail-closed invariants; the robot observes them but does not substitute evidence or force writes.
- Stateless edge/application services may be restarted after a failed health check; PostgreSQL, Redis and data-bearing jobs are alert-only unless a later reviewed policy explicitly approves an action.

