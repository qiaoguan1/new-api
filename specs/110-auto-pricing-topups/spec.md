# Specification: daily trusted pricing and upstream recharge aggregation

## Goal

Guarantee that every model with complete, recent, task-backed actual-cost
evidence is repriced automatically each Beijing business day, then produce an
auditable aggregate of upstream recharge transactions without inferring money
from balances or usage.

## Requirements

1. The daily chain runs in Beijing time: collect, audit, then apply pricing.
2. Text/image models with complete healthy-source evidence use actual cost
   multiplied by 1.5. Official video pricing stays on its separate path.
3. A failed source blocks only models that depend on that source. Models with
   no trusted actual cost retain their current price.
4. Every real apply run writes a pre-change backup, structured decisions, and
   a non-zero process status on failure.
5. Recharge aggregation accepts only authenticated recharge/order transaction
   records. Current balance, consumption, catalog price, and balance delta are
   reported separately and never counted as recharge.
6. Results expose provider, currency/unit, successful recharge total, record
   count, covered period, and a precise unavailable reason without credentials
   or payment identifiers.

## Safety boundaries

- Do not create recharge, payment, refund, or paid video activity.
- Do not log or commit credentials, cookies, session tokens, or raw orders.
- Do not modify CLR or re-enable excluded providers.

## Acceptance

- Relevant automated tests pass.
- A production dry run and apply run complete with matching eligible decisions.
- The root cron has one authoritative daily chain in `Asia/Shanghai`.
- Recharge totals are manually reconciled against returned successful records;
  unknown providers remain explicitly unknown.
