# Feature Specification: Trusted Underpriced-Model Self-Correction

**Issue**: #19  
**Status**: In Progress

## Problem

The daily audit emits a critical `price_below_upstream_input` alert when the
current site price is below an upstream cost. The pricing worker currently
classifies every model-scoped critical alert as permanently blocked. Therefore
the exact condition that requires a safe price increase also prevents the
worker from calculating that increase.

## Requirements

1. A model whose only model-scoped critical alerts are recognized underpricing
   alerts may enter pricing calculation.
2. The target must use the highest valid actual input, output, or fixed cost
   from enabled, healthy, audited upstream sources with a complete dated
   billing-log collection.
3. The base price remains actual cost multiplied by 10 and the customer group
   ratio remains 0.15, producing a customer price of actual cost multiplied by
   1.5.
4. Missing credentials, incomplete collections, unavailable channels,
   ambiguous billing kinds, unrelated critical alerts, invalid costs, and the
   configured maximum-change guard continue to fail closed.
5. A recovery decision must state that it is an underpricing self-correction
   and record the alert types, selected costs and sources, old price settings,
   new price settings, and resulting customer price.
6. Deployment must be preceded by a backup and dry run. The live result for
   `gpt-5.6-sol` must be checked against the upstream ledger and NewAPI options.
7. Ten post-deployment validation rounds must pass without changing models that
   lack trustworthy actual cost.
8. The daily audit must compare the site sell price with complete model-level
   actual billing cost. An upstream catalog/list price must not be labeled as
   actual cost or create a critical underpricing alert.
9. The production crontab must contain Unix LF line endings and no literal
   `\r` suffix so the Beijing 08:40 pricing command reaches the worker.

## Acceptance Scenarios

- Given complete trusted costs and only a recognized underpricing alert, the
  model is repriced from the worst eligible costs.
- Given the same alert plus another critical model alert, the model remains
  blocked.
- Given an underpricing alert but an incomplete expected source, no price is
  changed.
- Given a proposed change above the movement limit, no price is changed.
- Given an unrelated healthy model on the same channel, it remains independently
  eligible.
- Given a catalog price above the site price but complete actual cost below the
  site price, the audit emits no critical underpricing alert.
- Given a complete actual input or output cost above the corresponding site
  price, the audit emits a recoverable critical actual-cost alert.
