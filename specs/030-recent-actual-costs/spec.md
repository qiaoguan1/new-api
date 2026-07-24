# Recent Trusted Actual Costs

**Issue**: [#30](https://github.com/qiaoguan1/new-api/issues/30)

## Goal

Keep actual-cost pricing available for an enabled model that had no upstream
usage yesterday by reusing the most recent trustworthy model-level deduction
sample from the last seven complete Beijing business days.

## Requirements

1. The target day's collection-completeness gate for every configured upstream
   account remains mandatory and unchanged.
2. If any healthy enabled source has a target-day sample for the model, use
   target-day samples only. Otherwise, for each healthy source select at most
   one sample: its newest complete `per_model_real_cost` entry between target
   day minus one and target day minus six.
3. Never use catalog prices, metadata prices, balance deltas, incomplete days,
   future dates, disabled/failed sources, or non-positive costs.
4. Continue selecting the highest eligible input, output, or fixed cost across
   the resulting same-basis healthy-source samples.
5. Record sample dates, a `current_day_actual` or `recent_actual` basis, and the
   seven-day window in every applied decision.
6. Preserve billing-kind ambiguity, price-change, critical alert, channel
   health, and atomic database transaction guards.
7. Do not manufacture paid requests to create samples.
8. Explicit database `CompletionRatio` values override family fallbacks in the
   running NewAPI process, so a successful pricing transaction is the value
   actually used for billing and exposed by `/api/pricing`.
9. Current healthy enabled channel configuration is the only model inventory.
   Historical billing can price that inventory but cannot resurrect a model
   that appears only in upstream history.

## Pricing invariant

NewAPI base price remains trusted actual cost times 10; group ratio remains
0.15; customer price remains trusted actual cost times 1.5.
