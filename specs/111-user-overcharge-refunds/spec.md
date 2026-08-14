# Specification: evidence-bound historical overcharge refunds

## Goal

Recalculate every eligible historical consumption record using the actual cost
of the upstream channel that served that record, and credit only the positive
difference above actual cost multiplied by 1.5.

## Requirements

1. Every evaluated consumption log is bound to its persisted `channel_id`, and
   that channel is resolved to exactly one configured upstream slug.
2. Cost evidence must match the Beijing business date, upstream slug, normalized
   model name, and billing kind of the consumption log. Evidence from another
   channel or date must never be substituted.
3. The upstream ledger entry must be `collection_status=complete` and
   `actual_log_complete=true`. Missing, incomplete, ambiguous, zero-sample, or
   kind-mismatched evidence fails closed and produces no refund.
   Fixed-per-call refunds additionally require an explicitly verified uniform
   source/model contract; variable daily averages are not exact task evidence.
4. Refund quota is `max(original quota - round(actual CNY cost × 1.5 × 500000), 0)`.
   Token cache ratios recorded on the original log remain part of text-cost
   reconstruction. Fixed-price/image evidence applies only to fixed/task logs.
5. Existing rows in `historical_pricing_refunds` are immutable and skipped by
   `source_log_id`. A frozen plan contains source fingerprints, channel mapping,
   evidence, totals, and a deterministic SHA-256 digest.
6. Live execution requires an explicit plan SHA, stopped public ingress, a
   pre-write backup, and one PostgreSQL transaction. It updates wallet and
   token accounting, inserts immutable audit rows and one compensation log per
   user, then invalidates only affected quota caches.
7. Execution and post-check reports expose user IDs/usernames and monetary
   totals but never tokens, prompts, request payloads, payment identifiers, or
   upstream credentials.

## Non-goals

- Estimating costs from current catalog prices, balance deltas, another
  upstream, or another day.
- Reversing legitimate high-volume or long-context usage.
- Repricing video tasks already settled by the video gateway.
- Re-refunding rows handled by earlier compensation runs.

## Acceptance

- An unrelated incomplete upstream no longer blocks a complete source/day/model.
- An incomplete actual source still blocks its own logs.
- Source, date, model, and billing-kind mismatches are covered by tests.
- Dry-run totals reconcile exactly to refund rows and per-user totals.
- Transaction validation rolls back with the same calculated result as apply.
- Production apply is idempotent and the second execution creates zero refunds.
- A post-check proves each credited wallet delta and audit count.
