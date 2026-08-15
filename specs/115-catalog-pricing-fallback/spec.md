# Specification: authenticated catalog pricing fallback

## Goal

Price active text and image models from authenticated upstream catalog metadata
when task-backed actual-cost evidence is unavailable, while preserving the
existing 1.5 markup, channel isolation, and official video-pricing boundary.

## Requirements

1. Task-backed actual cost and the current authenticated catalog are both
   accepted evidence. When both exist for one source and billing kind, the
   higher normalized cost is the safe price floor.
2. A source without actual evidence may contribute authenticated catalog
   metadata only when its currency, account group, billing kind, and unit can
   be normalized without inference.
3. Text catalog ratios normalize to CNY per million input/output tokens. Fixed
   image prices normalize to CNY per call. Mixed or unknown kinds fail closed.
4. Every active healthy source for a model must have usable actual or catalog
   evidence; disabled and failed channels do not influence price.
5. The highest normalized source cost determines the base price. The existing
   group ratio produces a customer price of exactly cost multiplied by 1.5.
6. Video models remain protected by the independent Ark official-price worker.
7. Private run logs record evidence type, source, and sample time without
   credentials or raw billing records.
8. Normalized text cost is bounded at CNY 100,000 per million tokens, fixed
   cost at CNY 10,000 per call, and completion ratio at 1,000. Values outside
   those bounds fail closed before arithmetic or JSON serialization.

## Acceptance

- Actual and catalog evidence for the same source use the higher normalized
  cost, so a recent catalog increase cannot be hidden by an older usage sample.
- Catalog-only text and fixed image models receive deterministic prices.
- Multi-source models use the highest normalized input, output, or fixed cost.
- Missing units, invalid group ratios, stale metadata, and kind conflicts retain
  the current price with an explicit reason.
- Existing video and actual-cost tests continue to pass.

## Safety boundaries

- Do not modify CLR.
- Do not create paid tasks or infer prices from balances, usage deltas, or
  unauthenticated webpages.
- Do not mix per-token, per-second, and per-call values.
