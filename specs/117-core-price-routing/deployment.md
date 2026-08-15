# Production deployment record

## Execution

- Deployed: 2026-08-16 02:28 CST
- Source PRs: #118, #119, #120, #121
- Business day: 2026-08-15
- General rollback backup:
  `/opt/ai-api-stack/backups/issue117-core-price-routing-20260816-021948`
- Pricing option backup:
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-08-15-20260816T022820+0800.json`
- Deployed pricing worker SHA256:
  `3b266778d217458bf260bab2153d81f933f7fd6021a5f270f37f9e2252072ab2`
- Deployed evidence SHA256:
  `57fd2dd23b1b2ba97e995c796f1f9ff50893c423e15ffb2c5207256dd2f90945`

## Retained routes

- `gpt-5.5`, `gpt-5.6-sol`: Code Plan and Hanhe priority 4, JojoCode priority 3,
  Maolao priority 2.
- `gpt-image-2`: Maolao priority 3, Hanhe priority 2, JojoCode priority 1.
- Database verification found no other channel carrying any of these three
  model names.

## Applied prices

- `gpt-5.5`: CNY 2.3175/M input, CNY 13.905/M output.
- `gpt-5.6-sol`: CNY 2.3175/M input, CNY 13.905/M output.
- `gpt-image-2`: CNY 0.1545/image for 1K, 2K, and 4K. Each resolution was
  evaluated separately; the maximum retained cost is CNY 0.103 for all three.

The targeted live run discovered three models, applied three, and skipped zero.
A database comparison against the pre-change backup confirmed every non-target
pricing option remained byte-for-byte equivalent after JSON parsing.

## Verification

- Full channel-monitor test suite: 152/152 passed.
- Production targeted dry-run before apply: 3/3 apply, 0 skip.
- Production targeted dry-run after apply: all old and proposed values equal.
- Ten no-charge health rounds: API root, API status, relay health, relay ready,
  and NewAPI container health passed in every round.
- Root cron remains in `Asia/Shanghai`: collect 08:20, audit 08:30, price 08:40.
