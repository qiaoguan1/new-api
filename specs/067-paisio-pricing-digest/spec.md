# Feature Specification: Paisio Settlement, Isolated Pricing, and Daily Operations Digest

**Feature Branch**: `codex/issue-67-paisio-pricing-digest`  
**Created**: 2026-08-12  
**Status**: In Progress  
**Input**: Restore safely audited Paisio video routing, isolate automatic pricing failures by channel and model, and email a daily operations digest.

## User Scenarios & Testing

### User Story 1 - Settle Paisio from its real task ledger (Priority: P1)

As the relay operator, I want a successful Paisio video task to settle from its exact authenticated billing rows, so Paisio can carry production traffic without using catalog prices or task-count fields as cost.

**Independent Test**: Return one terminal task from `/api/task/self` and exact filtered rows from `/api/log/self?request_id=<task_id>`; prove the collector sums only matching billing rows and rejects missing, ambiguous, non-terminal, refunded-success, or malformed evidence.

### User Story 2 - Isolate pricing failures (Priority: P1)

As the pricing operator, I want a failed upstream collection to block only models that depend on that upstream, so unrelated trusted text and image models continue their daily update.

**Independent Test**: Combine one incomplete channel with one unrelated complete channel; prove the affected model is preserved while the unrelated model is applied in the same atomic run and every recognized video model remains protected.

### User Story 3 - Receive one daily operations digest (Priority: P1)

As the operator, I want one Beijing-time email after the daily collection and pricing workflow, so I can review channel balances, calls, costs, collection failures, and pricing actions without logging into the server.

**Independent Test**: Build a digest from private fixture ledgers and pricing logs; prove the structured payload contains every configured channel, daily and month-to-date usage, pricing counts, safe anomaly codes, and is delivered once per business date with retry-safe state.

## Requirements

- **FR-001**: Paisio task status MUST come from an authenticated exact task lookup and actual cost MUST come from authenticated billing rows whose `request_id` exactly equals the persisted provider task ID.
- **FR-002**: Paisio task field `quota` MUST NOT be interpreted as money.
- **FR-003**: Settlement MUST require one unique terminal successful task and a bounded, complete, internally consistent billing-row set; otherwise it MUST remain pending.
- **FR-004**: New v2.1 Paisio traffic MUST remain disabled until a read-only production reconciliation passes, then may be added to the independent approved-provider set.
- **FR-005**: Automatic text/image pricing MUST evaluate collection completeness only for each model's expected enabled sources; unrelated incomplete credentials MUST NOT abort the run.
- **FR-006**: Recognized video models MUST remain excluded from generic upstream-cost pricing.
- **FR-007**: The digest MUST use Asia/Shanghai business dates and include every configured channel's current balance state, prior-day calls/cost, month-to-date calls/cost, collection status, audit status, and pricing applied/skipped/blocked totals.
- **FR-008**: Unknown collection MUST remain unknown, never zero; all names and error codes sent to email MUST be length-bounded and HTML-escaped.
- **FR-009**: The email recipient MUST be server-side configuration, not request input; credentials, URLs, prompts, output media, tokens, and raw exceptions MUST never enter the digest.
- **FR-010**: Digest delivery MUST be idempotent per business date and retryable after failure.

## Success Criteria

- **SC-001**: Paisio fixture and live read-only probes produce the same exact task cost from request-scoped billing rows.
- **SC-002**: A mixed complete/incomplete pricing fixture updates unrelated trusted models and preserves affected models in one successful run.
- **SC-003**: A real test digest reaches the configured mailbox, followed by ten production verification rounds with zero secret, settlement, pricing-isolation, or duplicate-email invariant failures.

