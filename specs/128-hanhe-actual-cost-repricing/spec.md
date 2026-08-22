# Specification: Hanhe actual-cost repricing

## Goal

Use each enabled upstream's real billed cost whenever trustworthy request-level
evidence exists, correct Hanhe's `actual_cost` semantics, and reprice every
currently routed text and image model at exactly 1.5 times the highest trusted
cost among its enabled model-matched routes.

## Requirements

1. `usage_v1` records use `actual_cost` as the billed amount. `total_cost` is
   retained only as the undiscounted reference amount for reconciliation.
2. Hanhe aggregation must preserve complete pagination, exact daily totals,
   per-model call counts, and separate text input/output or fixed image units.
3. For every enabled routed text or image model, recent trustworthy actual cost
   takes precedence for that source. An authenticated catalog price may fill a
   source only when no recent actual sample exists and its unit is unambiguous.
4. The highest selected source cost is multiplied by 1.5. Missing sources,
   malformed evidence, mixed units, implausible values, or incomplete active
   collections fail closed and retain the existing price.
5. Official video pricing remains independent. CLR, credentials, raw billing
   records, disabled channels, and unapproved providers are outside this change.
6. Production writes require a full database backup, a reviewed dry-run, an
   auditable per-model decision, an idempotent second run, and ten no-charge
   health verification rounds.

## Acceptance

- A fixture containing both `actual_cost` and `total_cost` proves the billed
  total and per-model evidence use `actual_cost` only.
- Hanhe 2026-08-21 reconciliation resolves to CNY 18.52698844 rather than CNY
  92.6349422, while preserving 465 rows.
- Enabled routed text and image models use actual-first source evidence and
  produce exactly a 1.5 customer markup.
- No video option changes, no disabled-channel evidence enters a decision, and
  a second production run is idempotent.

## Safety boundaries

- Do not infer cost from balance deltas.
- Do not send paid generation requests.
- Do not log or commit credentials or raw account records.
- Do not lower a price when any active expected source lacks trustworthy
  comparable evidence.
