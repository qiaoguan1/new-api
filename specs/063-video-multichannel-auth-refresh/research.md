# Research: Audited Video Multi-Channel Routing and Safe Auth Refresh

## Initial production facts

- The reviewed gateway adapter set is Paisio, RollDek and Toonflow.
- Production currently enables only Toonflow, so no cross-provider fallback is active.
- The checked-in catalog already prefers Paisio before Toonflow for standard and fast variants; Mini is Toonflow-only.
- Production exact settlement currently works for Toonflow through its authenticated operation log.
- Toonflow uses a separate Web token for billing. The current token is a JWT with a finite expiry and was obtained through an operator-authorized CAPTCHA login.
- Existing API-key credentials are static until the provider revokes or rotates them; they require scheduled readiness checks rather than blind periodic replacement.
- PackAPI and Unity2 are explicitly out of scope and must remain excluded even if discovered.

## Decisions

### Eligibility is stricter than adapter availability

An adapter with an API key is insufficient. New v2.1 traffic requires a compatible reviewed route and an exact task-level billing collector, otherwise final “actual cost ×1.5” settlement cannot close.

### Do not bypass CAPTCHA

If Toonflow does not expose an approved refresh-token API, the safe implementation is scheduled expiry inspection, advance alerts and an atomic operator token replacement command. Automated username/password/CAPTCHA evasion is prohibited.

### Keep refresh separate from task handling

Credential mutation and notification run outside the request path. The gateway consumes validated credential files or injected values and dynamically removes an expired provider from new routes without stopping historical work.

### Preserve immutable route plans

New eligibility changes affect only new tasks. Existing queued/running jobs retain their stored route plan and upstream identity.

## Verified production evidence

- All three generation credentials authenticate successfully. Paisio exposes 47 generation models,
  RollDek exposes 19, and Toonflow has 15 successful production tasks in the durable v2.1 ledger.
- Only Toonflow currently meets the complete production eligibility rule. Its authenticated operation
  log supports an exact unique lookup by provider task ID and has repeatedly reconciled to settlement.
- Paisio exposes `/api/task/self?task_id=...`, but its latest non-empty daily task evidence failed the
  independent complete-ledger reconciliation (`cost_mismatch`). Its collector is implemented and can
  be monitored dark, but production traffic remains fail-closed until a clean reconciliation sample.
- RollDek exposes the same authenticated task endpoint, but the account has no historical video task
  evidence and the reviewed stable catalog contains no RollDek route. It is registered and monitored,
  not eligible for new v2.1 work.
- The existing settlement publisher carries the persisted v2/v2.1 contract version; it does not need
  a legacy-contract rewrite.
- Toonflow issues a 30-day JWT through CAPTCHA-bound console login. Its current expiry is
  2026-09-09 16:25:43 Beijing time. No refresh token or approved refresh endpoint exists in the
  current web client, so unattended refresh would be a CAPTCHA bypass and is not implemented.
- Paisio and RollDek account sessions can be renewed through their ordinary server login endpoint and
  verified by a read-only single-row task query. The gateway reloads these short-lease session files.
- The running v2.1 gateway was previously deployed from an untracked release directory. The tracked
  compose deployment now binds persistent SQLite and read-only credential files so recreation does not
  lose configuration.
