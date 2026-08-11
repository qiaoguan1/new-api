# Implementation Plan: XingTu Video Contract v2.1

## Technical Context

- Go, Gin, GORM, Redis-compatible quota cache, and the Python durable video gateway.
- Base branch already contains v2 official pricing, settlement evidence, result withholding,
  provider routing, and signed webhook outbox delivery.
- Security-sensitive scope: public request parsing, wallet/token/subscription mutation, and result
  authorization.

## Design

1. Centralize legacy/current XingTu contract constants and compatibility checks.
2. Add an atomic wallet reservation operation that requires `quota >= amount` in both cache and DB
   paths. Enable it for every official video reservation.
3. Apply the same hard check to positive settlement supplements; on insufficiency roll back the
   transaction and persist `payment_required` without deducting any source.
4. Treat only `settled` as result-ready. Translate legacy `settled_with_debt` to public
   `payment_required` without exposing a downloadable result.
5. Install a route-scoped body limiter before `TokenAuth` and `Distribute`, activated for every
   XingTu-contract-tagged create request so an obsolete or unknown version cannot bypass it.
6. Preserve explicit audio booleans and cover `true` through validation and adapter tests.
7. Keep dual-read support for existing v2 tasks and settlement evidence while publishing v2.1 for
   newly accepted requests.
8. Update the canonical protocol, migration notes, and downstream handoff checklist.

## Rollout

1. Back up production source, database, compose file, gateway state, and environment metadata.
2. Dry-run database compatibility and inspect existing v2/debt task counts without exposing user
   data.
3. Deploy gateway and NewAPI from the same reviewed merge commit.
4. Configure v2.1 callback/test values only from root-readable server secret files.
5. Run non-billable contract, oversize, auth, callback, query, and content-withholding probes.
6. Keep the prior image and backup path for immediate rollback.
