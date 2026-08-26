# Feature Specification: All-Channel, All-Model Automatic Pricing

**Feature Branch**: `codex/issue-3-all-model-pricing`
**Created**: 2026-07-23
**Status**: In Progress
**Issue**: https://github.com/qiaoguan1/new-api/issues/3

## Goal

Automatically discover every model exposed by enabled upstream channels and update every model
that has trustworthy, model-level actual-cost evidence. The customer price must equal the highest
eligible upstream actual cost multiplied by 1.5.

## Pricing Contract

- Standard/base price = highest eligible actual cost x 10.
- The configured frontend group ratio must be 0.15.
- Customer price = standard/base price x 0.15 = actual cost x 1.5.
- Text models have independent input and output costs.
- Per-request models use a fixed `ModelPrice`.
- A model without actual-cost evidence is inventoried and skipped; its existing price is retained.

## User Scenarios and Testing

### User Story 1 - Discover every eligible model (P1)

As the operator, I need the daily job to derive its model inventory from enabled channel audit data
and actual upstream billing logs instead of a hard-coded allowlist.

**Acceptance scenarios**:

1. Given enabled, healthy channels containing previously unknown models, the dry-run includes those
   models without a source-code change.
2. Given a disabled or failed channel, its cost samples cannot influence a pricing decision.
3. Given a discovered model with no actual-cost sample, the result records `skip` and preserves the
   current price.

### User Story 2 - Apply the exact 1.5x pricing rule safely (P1)

As the operator, I need every applied decision to produce a customer price exactly 1.5 times the
highest eligible actual cost.

**Acceptance scenarios**:

1. Text input and output prices independently equal their highest eligible actual costs x 1.5.
2. Fixed-price model prices equal the highest eligible per-call actual cost x 10, which becomes
   cost x 1.5 under group ratio 0.15.
3. A group-ratio mismatch, stale audit, malformed cost, ambiguous billing kind, or excessive price
   movement prevents the affected write and produces a structured reason.

### User Story 3 - Make daily application atomic and observable (P1)

As the operator, I need audit failures to gate price application and database failures to roll back
the complete pricing change.

**Acceptance scenarios**:

1. The 08:40 job requires the matching 08:30 audit date and excludes channels/models with critical
   alerts.
2. `ModelRatio`, `CompletionRatio`, and `ModelPrice` are updated in one transaction with strict
   command and affected-row validation.
3. A pricing-only backup and structured run log are written before/after application; dry-run never
   writes the database.

### User Story 4 - Honor configured completion ratios at runtime (P1)

As the operator, I need a configured `CompletionRatio` to override a built-in model-family default,
so newly discovered models do not require another source patch.

**Acceptance scenarios**:

1. A configured completion ratio for any model, including `gpt-5.6-sol`, is returned and reported
   as unlocked.
2. When no configured value exists, the existing hard-coded family default remains a fallback.

## Functional Requirements

- **FR-001**: The pricing job MUST NOT contain a managed-model allowlist.
- **FR-002**: The model inventory MUST be the union of models seen in enabled healthy channel audit
  data and models with actual-cost entries for eligible upstreams.
- **FR-003**: Only positive finite model-level actual costs from eligible upstreams may be applied.
- **FR-004**: The highest eligible cost MUST be used independently for text input/output and fixed
  per-call pricing.
- **FR-005**: The job MUST verify the configured group ratios used for sale are 0.15.
- **FR-006**: The matching daily audit MUST gate price application; critical model/channel alerts
  exclude affected candidates and a global critical alert aborts application.
- **FR-007**: Missing or untrusted costs MUST retain current prices.
- **FR-008**: Price writes MUST be atomic and fail closed on command, parse, or affected-row errors.
- **FR-009**: The job MUST support dry-run, a configurable movement limit, backup, and structured
  history without recording credentials or raw billing logs.
- **FR-010**: Explicit database completion ratios MUST take precedence over family defaults.
- **FR-011**: No daily probe may create paid text, image, audio, or video usage without separate
  operator authorization.

## Success Criteria

- Synthetic coverage demonstrates automatic pricing for newly introduced text and fixed models.
- Every applied decision proves customer price / highest cost = 1.5 within rounding tolerance.
- Tests prove disabled/failed channels, stale audits, missing costs, and transaction failures do not
  alter prices.
- Runtime pricing reports the configured `gpt-5.6-sol` completion ratio after deployment.
- No credential, runtime ledger, or production database content is tracked.
