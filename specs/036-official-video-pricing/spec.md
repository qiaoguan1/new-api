# Official Video Pricing and Cost Monitoring

**Issue**: [#36](https://github.com/qiaoguan1/new-api/issues/36)

## Goal

Charge every downstream Seedance 2.0 video request from a versioned official
price catalog with a fixed 1.5 markup. Upstream catalog prices and actual
deductions remain internal cost evidence and can never select or lower the
downstream sale price.

## Authoritative price catalog

The initial catalog records the 2026-08-09 Volcano Engine CNY list prices:

| Stable model | Resolution | No video input | With video input |
|---|---|---:|---:|
| `seedance-2.0` | 480P/720P | CNY 46/M tokens | CNY 28/M tokens |
| `seedance-2.0` | 1080P | CNY 51/M tokens | CNY 31/M tokens |
| `seedance-2.0-fast` | 480P/720P | CNY 37/M tokens | CNY 22/M tokens |
| `seedance-2.0-mini` | 480P/720P | CNY 23/M tokens | CNY 14/M tokens |

The catalog also records the source URL, checked time, currency, formula
revision, markup, supported durations, resolutions, frame rate, and dimensions.
Changing any price or formula requires a catalog revision. Each verification
expires within 45 days so an indefinitely stale official price cannot continue
writing production prices.

## Requirements

1. A quote is computed as official tokens for the normalized request multiplied
   by the catalog CNY rate and then by exactly 1.5.
2. No-video-input tokens use
   `output_seconds * width * height * fps / 1024`. Video-input quotes fail closed
   until the official minimum-token table for that request is configured; they
   must not use a knowingly incomplete estimate.
3. The current relay supports 4-15 output seconds and reviewed resolution/aspect
   combinations only. Invalid model, duration, resolution, aspect ratio, or
   missing official price fails before quota is frozen.
4. NewAPI `ModelPrice` base prices for reviewed raw video routes represent one
   output second. The task billing duration multiplier produces the final
   request charge. The configured customer group ratio must therefore yield the
   same catalog quote after the 1.5 markup.
5. The generic upstream-cost auto-pricer must skip every recognized video model.
   A separate official-video pricing plan is independent of the upstream ledger
   and may not accept upstream prices as a fallback.
6. Classic NewAPI logs must distinguish per-second deductions, failed refunds,
   successful completion markers, and per-call deductions. `net cost / log row`
   is never emitted as a video unit cost.
7. Authenticated upstream catalog prices retain `model_price`, `quota_type`, and
   `billing_mode`. They are labelled catalog evidence, separately from actual
   deduction evidence.
8. Upstream evidence is keyed by source plus exact raw model. Evidence from one
   raw model cannot be borrowed by a differently named route.
9. The internal manifest contains the official sale basis, exact upstream cost
   evidence when available, and comparable duration-level profit examples. Unit
   mismatches are explicit and are never converted by guessing.
10. The public capability output may expose official sale-rate metadata but
    never source names, raw model names, channel IDs, upstream costs, margins,
    credentials, or review notes.
11. A missing official price blocks route publication and official price writes.
    Missing upstream cost only marks profit comparison unavailable; it does not
    block an otherwise healthy officially priced route.
12. Downstream video task queries expose a provider-neutral `usage` object. It
    includes provider-reported output/total tokens when available, the final CNY
    charge after success, the reserved amount while pending, the refunded amount
    after failure, and an explicit billing status. It never exposes upstream
    routing, provider costs, margins, or credentials. Existing response fields
    remain backward compatible.

## Non-goals

- Changing text or image auto-pricing.
- Treating an upstream catalog quote as an actual deduction.
- Manufacturing a paid video request to obtain a cost sample.
- Supporting video-input pricing before the official minimum-token lookup is
  represented in the catalog.
