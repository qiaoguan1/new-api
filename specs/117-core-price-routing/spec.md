# Specification: core model route and price policy

## Goal

Keep only three or four verified upstream routes for each core model, route
from lower cost to higher cost, and charge customers at exactly 1.5 times the
highest trusted cost among the retained routes.

## Requirements

1. The current scope is `gpt-5.5`, `gpt-5.6-sol`, and `gpt-image-2`.
2. Each scoped model has three or four enabled upstream routes. Every other
   channel must not participate in routing that model.
3. Route priority uses comparable normalized costs and NewAPI's higher numeric
   priority is called first. Ties use a stable explicit order.
4. For the three explicitly scoped models, a source's recent task-backed actual
   cost takes precedence. An authenticated model-catalog price is used only
   when that source has no recent actual cost. Every unlisted model retains the
   existing conservative actual-plus-catalog maximum rule.
5. The highest selected source cost determines the global model price. With
   the existing group ratio of `0.15`, the customer price is cost multiplied by
   `1.5`.
6. Text input and output costs are selected independently. Fixed image costs
   are evaluated for every supported resolution. A single fixed price may be
   written only when the highest retained source cost is identical for every
   resolution. Units must never be mixed.
7. Manual authenticated-catalog evidence is allowed only when it records the
   source, model, normalized unit, verification day, expiry day, and positive
   bounded cost. Expired or malformed evidence fails closed.
8. Existing video pricing remains outside this policy.
9. Production writes require a rollback backup and a dry-run review. No paid
   model request is part of verification.
10. The production maintenance run must accept an explicit target-model set so
    no unrelated model option can be changed by this scoped deployment.

## Acceptance

- Actual cost wins over a higher catalog value for the same source.
- Catalog evidence fills only sources without recent actual cost.
- The selected routes' maximum actual-or-fallback cost produces exactly a 1.5
  customer multiplier.
- `gpt-image-2` 1K, 2K, and 4K are evaluated independently. The current
  retained set produces the same CNY 0.103 maximum for all three resolutions,
  so their customer charge is CNY 0.1545 per image.
- The production database contains only the approved routes for each scoped
  model and priorities match the reviewed low-to-high order.
- A second run is idempotent and the next scheduled pricing run retains the
  same evidence-selection contract.

## Safety boundaries

- Do not modify CLR.
- Do not expose upstream credentials or raw billing records.
- Do not infer a price from account balance deltas.
- Do not create paid requests during deployment or verification.
