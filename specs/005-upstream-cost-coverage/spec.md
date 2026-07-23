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

## Success Criteria

- All recognized account groups are saved with verified file permissions and a rollback copy.
- The old 729/724 result is replaced by an explainable count tied to enabled channel configuration;
  any remaining skips identify a concrete lack of model-level actual-cost evidence.
- `gpt-image-2` is probed through image-channel configuration and is either priced from trustworthy
  cost evidence or retained with an accurate upstream failure reason.
- Tests, production dry-run, sequential live run, database checks, and public pricing checks pass.

