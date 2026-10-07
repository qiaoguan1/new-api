# Claude Fable 5.1 incremental integration

User request: enable and fully integrate the exact `claude-fable-5-1` model.
Tracking: https://github.com/qiaoguan1/new-api/issues/187

## Requirements

- Validate the exact upstream label and an actual response. Never rename Fable 5,
  Sonnet or Opus as Fable 5.1.
- Prefer the lowest verified costs among stable candidates and retain at most two
  qualified providers. Preserve their request-dependent tariff differences.
- Retail is the highest retained verified cost for the same request, multiplied
  by 1.5. Keep input, output, cache read, 5-minute cache creation and 1-hour cache
  creation separate where the upstream contract differentiates them.
- Publish to the existing text/automatic routing policy for valid users. Do not
  revive disabled or expired tokens, change key strings, remove IP restrictions,
  or change other model prices, user balances or video configuration.
- Stage configuration privately, verify runtime billing, then activate. Retain a
  scoped backup and independent public-directory and billing verification.

## Initial evidence (2026-10-07)

The production DB and public directory have no route for the exact ID.
Authenticated/readable upstream catalogs advertise it on Haina, Rolldek,
Maolao and NodyHub. Code Plan does not list it; Paisio lists only Fable 5.
Catalog presence is not a generation-success claim.

Haina's saved login is rejected on both its website and API origin. Its historical
manual-evidence policy remains unchanged; no password reset or speculative
cash-price conversion is authorized by this integration.

## Acceptance

1. Exact-model upstream generation and trusted actual tariff evidence.
2. Correct retained-route pricing, including cache and request multipliers.
3. Scoped staged rollout with concurrency guards and rollback evidence.
4. Ordinary/automatic and existing user-key model access verification.
5. Real bounded relay test with wallet/log/formula reconciliation; disclose
   unsupported capabilities and any external prerequisite honestly.

## Authorized option A

The user selected A after the protocol/effort billing risk was disclosed.
Implement exact-model-only effort conversion/validation and cache settlement
compatibility, validate on the immutable runtime baseline, then publish Rolldek
ccmax primary and Maolao group_4 backup for valid user keys. Other model behavior
and prices, user balances, key strings and video configuration stay unchanged.
