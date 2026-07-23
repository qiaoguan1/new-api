# Implementation Plan: All-Channel, All-Model Automatic Pricing

**Branch**: `codex/issue-3-all-model-pricing`
**Spec**: `specs/003-all-model-pricing/spec.md`

## Technical Approach

1. Version the pricing worker under `ops/channel-monitor/` without credentials or runtime data.
2. Split discovery, audit gating, cost selection, calculation, and persistence into testable Python
   functions using only the standard library.
3. Discover models dynamically from the matching daily audit and actual-cost ledger. Treat existing
   `kind=image` ledger records as backward-compatible fixed-per-call costs.
4. Build all three NewAPI option objects in memory and commit them in one PostgreSQL transaction.
5. Prefer explicitly configured completion ratios in NewAPI while retaining hard-coded ratios as
   fallback defaults.
6. Validate locally, run a production dry-run, back up pricing options, then deploy with a verified
   rollback copy.

## Safety Decisions

- No automatic paid probing of every model.
- No price change when the daily audit is missing/stale or cost evidence is absent.
- No use of disabled, unavailable, critically alerted, or unmapped upstream channels.
- No credentials, raw ledger, runtime logs, or database dumps in Git.
- Production application follows a successful dry-run and an explicit pricing backup.

## Verification

- Python unit tests for inventory, eligibility, text/fixed formulas, movement limits, and SQL errors.
- Go tests for configured completion-ratio precedence and hard-coded fallback.
- `git diff --check`, focused tests, relevant full tests, secret scan.
- Production dry-run and read-only comparison of generated decisions with current `/api/pricing`.
