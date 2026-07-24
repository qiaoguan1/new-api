# Feature Specification: Historical Overcharge Compensation

**Issue**: #21
**Status**: In Progress

## Problem

Some successful historical requests were charged above the site's intended
customer price of 1.5 times verified upstream cost. Every affected customer
must receive the positive difference without guessing missing costs, changing
legitimate requests, or permitting a second run to refund the same request.

## Requirements

1. Audit every successful consumption log that retains the original token,
   cache, model, group, and fixed-price billing parameters.
2. Resolve each request to its Beijing calendar day and normalized model name.
   Use only positive per-model actual-cost evidence from the upstream billing
   ledger. Complete audited collections are trusted; legacy records may be used
   only when they contain positive observed calls and explicit per-model actual
   costs, and must be reported separately.
   For a modern dated collection, every recorded expected upstream must be
   complete; one incomplete source blocks all refund calculations for that day.
3. For each day and model, use the highest verified actual input, output, or
   fixed cost across eligible observed upstream sources. Never use catalog,
   advertised, fallback, or invented prices.
4. Recalculate the policy quota as verified cost multiplied by 1.5, preserving
   the request's token/cache semantics and NewAPI rounding. Refund only
   `original quota - policy quota` when it is positive.
5. Requests without unambiguous actual-cost evidence remain unchanged and are
   included in a missing-evidence report. Text and fixed/image/video billing
   kinds must not be mixed.
6. Produce a dry-run report by source log and by user, including evidence day,
   model, selected worst cost and source, original quota, policy quota, and
   refund quota.
7. Before live execution, back up user/token balances and all source log IDs.
   Apply live compensation in one database transaction under an advisory lock.
   When batch updates are enabled, stop public ingress and allow at least two
   batch intervals to flush before execution; the executor must refuse live
   application while the public nginx service is running.
8. Credit compensation to the user's wallet regardless of the historical
   billing source, reduce the user's lifetime used quota by the same amount,
   leave request count unchanged, and reconcile the originating token's used
   and remaining quota when the token still exists. Token adjustment is capped
   at its current used quota because a historical token may already have been
   refunded, reset, or deleted; token used quota must never become negative and
   any wallet-only difference must be disclosed in the audit result.
9. Create an immutable refund audit row keyed uniquely by source consumption
   log. A rerun with the same scope must create zero new refunds.
10. Keep original consumption logs and upstream/channel consumption unchanged.
    Add explicit refund logs so gross usage and compensation remain auditable.
11. Invalidate only affected quota caches after a committed transaction.
12. Verify every affected user's before/after balance and aggregate totals, then
    pass ten independent post-execution validation rounds.

## Acceptance Scenarios

- A request charged above 1.5 times the highest verified model cost receives
  exactly the positive quota difference.
- A request already at or below policy price receives no refund.
- A request with no trustworthy actual-cost match receives no refund and is
  listed as missing evidence.
- A date with one rate-limited or otherwise incomplete expected upstream is
  blocked even when another source returned a lower actual cost.
- A text request uses its recorded uncached, cached, and completion token
  quantities; a fixed-price request uses only a matching fixed actual cost.
- Two observed sources with different costs use the higher cost, preventing an
  excessive refund caused by selecting a cheaper route.
- A second execution sees the unique source-log records and refunds zero.
- Any user balance underflow, missing user, duplicate source log, total mismatch, or
  transaction error aborts all live database mutations.
