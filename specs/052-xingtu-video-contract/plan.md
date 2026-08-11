# Implementation Plan: Unified XingTu Downstream Video Contract

## Summary

Add an opt-in v2 public contract around the existing exact-cost video billing engine. Normalize the
documented top-level request fields into provider metadata, add a durable per-user idempotency claim,
serialize one provider-neutral public DTO, and publish a deployment guide generated from the same
contract decisions.

## Technical Context

- Go 1.22+, Gin, GORM; SQLite, MySQL, and PostgreSQL.
- Existing `xtai-video-billing-v2` reservation and settlement state in `TaskBillingContext`.
- Existing token authentication and `/v1/videos` routes.
- CNY conversion remains `500000 quota/CNY`; public formatting is six decimal places.

## Constitution Check

- Backward compatible: v2 is selected by an explicit contract header.
- Fail closed: idempotency is claimed before external side effects; uncertain claims are not replayed.
- Provider neutral: public DTO contains user-side billing only.
- Exact money: strings are produced from integer quota, not binary floating-point contract values.
- Cross-database: new persistence uses GORM and portable indexes.
- Test first: contract and idempotency tests fail before implementation.

## Delivery Slices

1. Freeze the public request/response/error contract and downstream responsibilities.
2. Normalize top-level audio/aspect fields while preserving explicit false.
3. Add durable idempotency claim and replay/conflict behavior.
4. Add the canonical public DTO and use it for submit/query in v2 mode.
5. Publish the downstream deployment guide and machine-verifiable examples.
6. Run focused/full tests, comprehensive/security review, ten rounds, PR, CI, and merge.

## Rollout

Deploy the additive table and DTO code with legacy behavior unchanged. Enable the v2 header only in a
staging XingTu client, verify reservation/pending/final/failure cases, then enable production XingTu.
Do not run paid provider tasks in automated verification.

