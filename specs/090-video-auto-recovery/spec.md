# Feature Specification: Unattended Video Failure Recovery

**Issue**: [#90](https://github.com/qiaoguan1/new-api/issues/90)  
**Branch**: `codex/issue-90-video-auto-recovery`

## Objective

Close every video submission and generation failure without per-job operator decisions. The gateway reconciles provider tasks and authoritative billing evidence, selects a safe next route, aggregates all real provider costs, and reaches a terminal downstream result or deterministic bounded failure.

## Non-goals

- No external LLM is permitted to infer task, refund, or billing state.
- No CAPTCHA bypass or password automation.
- No fabricated zero-cost evidence.
- No unbounded retries or unlimited additional provider spend.

## Functional requirements

1. Every upstream attempt has a durable identity, route, provider, stable idempotency key, submit window, execution task ID, billing task ID, status, and evidence.
2. Submit timeouts and ambiguous 408/425/5xx responses enter reconciliation, not terminal manual review.
3. Reconciliation searches the provider's authenticated task/operation ledger for a unique attempt:
   - unique task found: bind it and resume polling;
   - authoritative terminal zero net cost and no successful task: advance route;
   - non-zero terminal cost: persist the cost, then advance only within configured attempt/cost guards;
   - unavailable or ambiguous evidence: retry with bounded backoff until the total recovery deadline.
4. A task created upstream is never abandoned silently. Terminal failure triggers billing/refund reconciliation.
5. All attempt costs, including failed attempts, are summed. The single final downstream charge equals aggregate real net provider cost × 1.5.
6. Route advancement is idempotent across concurrency and process restarts.
7. Maximum four upstream attempts, maximum eight-hour recovery window, and a configurable extra-cost guard prevent loops. Four attempts are required because Fast 720p currently has three Paisio candidates plus Toonflow.
8. If authoritative evidence cannot close before the deadline, the gateway automatically fails the downstream job, releases its reservation, records a safe administrator alert, and never requires an operator to choose a route. Unknown upstream cost remains an operator-side business risk and is never invented or charged downstream.
9. Existing v2.1 public fields remain compatible; recovery phase and attempt count may be added without exposing provider names, credentials, raw ledgers, or margins.
10. Credentials are read only from existing restricted runtime files/environment. Values never enter repository, logs, responses, or snapshots.

## Provider policy

### Paisio

Use the authenticated NewAPI task list and request ledger. Match by persisted submit window, route/model metadata, final media identity when available, and unique task record. The request ledger is the only monetary authority.

### Toonflow

Use the authenticated operation log and stable attempt identity/task code. The operation row is the task and monetary authority. If the provider cannot search by client idempotency key, the gateway must use a unique bounded time-window match; ambiguity remains in automatic reconciliation until deadline.

## Acceptance scenarios

- timeout but original task exists;
- timeout and authoritative evidence proves no task/cost;
- task later fails and is fully refunded;
- task later fails with non-zero net cost;
- first attempt costs money, second succeeds, aggregate settlement is exact;
- duplicate monitor runs and service restart;
- ambiguous provider rows;
- billing authorization temporarily unavailable;
- attempts/time/cost guard reached;
- both providers unhealthy;
- webhook retries remain idempotent.
