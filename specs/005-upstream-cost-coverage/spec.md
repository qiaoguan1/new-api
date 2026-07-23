# Feature Specification: Upstream Credential and Cost Coverage Repair

**Feature Branch**: `codex/issue-5-upstream-cost-coverage`  
**Created**: 2026-07-23  
**Status**: In Progress  
**Issue**: https://github.com/qiaoguan1/new-api/issues/5

## Goal

Securely import the operator-provided upstream accounts, make the daily audit inventory reflect
models intentionally configured on enabled local channels, and probe image channels with an image
model so valid `gpt-image-2` costs are not blocked by a text-model failure.

## Functional Requirements

- **FR-001**: Credentials MUST be stored only in the production credential file, owned by root and
  mode `0600`; secrets MUST NOT enter Git, Issue text, logs, test fixtures, or command output.
- **FR-002**: Each recognized account MUST retain its website host and recharge conversion rate.
  Import MUST be atomic, backed up, and preserve unrelated existing entries.
- **FR-003**: Account validation MUST isolate failures and report only slug, host, and status.
- **FR-004**: Enabled-channel inventory MUST come from the local channel model configuration after
  model mapping is applied. An upstream `/api/pricing` catalog MUST only enrich that inventory and
  MUST NOT add unrelated catalog models.
- **FR-005**: The audit MUST retain both configured model names and priced model details, including
  an explicit reason for configured models absent from the upstream pricing response.
- **FR-006**: Probe-model selection MUST prefer a valid explicit `test_model`; otherwise it MUST
  choose a configured model appropriate for the channel, including `gpt-image-2` for image-only
  channels. It MUST NOT silently fall back to `gpt-5.5` when that model is not configured.
- **FR-007**: Automatic pricing MUST only discover models from the audit's configured inventory and
  model-level actual-cost ledger. Missing trustworthy cost MUST retain the current price.
- **FR-008**: No repair step may manufacture paid usage across the model catalog. The manual run is
  limited to the existing one-probe-per-enabled-channel audit and real upstream billing logs.
- **FR-009**: Production changes require a timestamped rollback backup, dry-run, tests, sequential
  08:20/08:30/08:40 execution, and read-only verification of database and public pricing.
- **FR-010**: Every configured credential MUST produce a dated collection record. A successful
  zero-row query is a complete zero cost; login, captcha, pagination, or API failures MUST be
  represented as incomplete with a sanitized error and null cost.
- **FR-011**: The collector MUST support both classic NewAPI billing logs and the `/api/v1`
  auth/usage API used by aihua, apikeyfun, and token-bridge. It MUST use the amount deducted from
  the customer account (`total_cost`), not the upstream operator's internal `actual_cost`.
- **FR-012**: Automatic pricing MUST fail closed for every model whose enabled upstream set contains
  a missing or incomplete billing collection. Existing prices MUST be retained for those models.
- **FR-013**: Channel Monitor MUST show the same UTC date for upstream billing and local NewAPI
  usage, with one row per upstream containing collection state, actual deducted amount, local billed
  amount, delta, calls, source, and sanitized failure reason.
- **FR-014**: Reconciliation totals MUST disclose expected, complete, incomplete, and credentialless
  upstream counts. Incomplete costs MUST never be summed as zero or used to calculate a margin.

## Acceptance Scenarios

1. Given an image-only channel configured with `gpt-image-2` and no explicit test model, the audit
   selects `gpt-image-2`, not `gpt-5.5`.
2. Given a channel configured with two local models and an upstream catalog containing 700 models,
   the audit inventory contains exactly the two configured models.
3. Given a local-to-upstream model mapping, the catalog lookup uses the upstream alias while the
   audit and pricing decision retain the local model name.
4. Given a configured model missing from the upstream catalog, the model remains inventoried with
   an unavailable reason and cannot be repriced from guessed data.
5. Given a recognized account with valid credentials, the live fetch records its real balance/log
   status without exposing the username, password, cookie, or bill rows.
6. Given a valid usage query returning no rows, the ledger records `complete`, zero cost, and zero
   rows; given any query failure, it records `incomplete` and null cost.
7. Given an enabled channel mapped to an incomplete upstream collection, all models on that channel
   retain their current prices with reason `upstream_collection_incomplete`.
8. Given the monitor payload for 2026-07-22, every configured/credentialed/audited upstream appears
   in reconciliation and the local comparison query uses exactly 2026-07-22 UTC.

## Success Criteria

- All recognized account groups are saved with verified file permissions and a rollback copy.
- The old 729/724 result is replaced by an explainable count tied to enabled channel configuration;
  any remaining skips identify a concrete lack of model-level actual-cost evidence.
- `gpt-image-2` is probed through image-channel configuration and is either priced from trustworthy
  cost evidence or retained with an accurate upstream failure reason.
- Tests, production dry-run, sequential live run, database checks, and public pricing checks pass.
