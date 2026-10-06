# 2026-10-06 production verification

Deployed to `156.239.3.210` / `api.aixingtuyun.com`. No user-wallet migration, recharge, Jojo reactivation or video configuration change.

## Models and price evidence

| Model / scope | Actual upstream cost CNY | Retail CNY | Verification |
|---|---:|---:|---|
| GPT6.1 Sol, input <=272000 context | input0.30/output1.50 per1M tokens | input0.45/output2.25 per1M | Code Plan exact API/group + actual bill; native2.11s and74 quota |
| GPT6.1 Sol, input >272000 context | input0.60/output2.25 per1M | input0.90/output3.375 per1M | Live authenticated expression preserved, boundary unit tests |
| GPT6.1 base cache read/write | 0.015/0.375 per1M | 0.0225/0.5625 per1M | Native cache fields normalization +210-quota fake integration |
| GPT6.1 long-context cache read/write | 0.030/0.750 per1M | 0.045/1.125 per1M | Same exact live tariff and expression tests |
| Banana Pro, single1024x1024 text image | 0.08/image | 0.12/image | Rolldek exact Gemini Pro bill40000 quota; bridge23.27s,1024x1024,retail60000 quota |

GPT6.1 uses a permanent key limited to the exact model and server IP. Neither upstream labels nor these tests assert official-provider authenticity. The site denomination is CNY500000 quota per yuan; UI currency labels are not used to infer upstream cost.

Banana Pro is not an alias for Flash. Only the verified1K generation capability is enabled; unsupported2K rejects before submission. Existing Flash route stays unchanged. Toonflow is generation-only at the verified1K price, excluded before image-edits submission.

## Failure handling

- Definite quota/capability rejection: select a different eligible route under a bounded retry budget.
- Ambiguous transport/5xx/body outcome: no second generation POST; expose protected submission state and keep exact relay request ID.
- Failed native request: precharge refund tested exactly once. This is not an assertion that the upstream supplier also refunded.
- Gateway reference download failure: definitive no-generation failure; later transport/result errors retain uncertainty and reconciliation evidence.
- Reader/capacity errors do not trigger an irrelevant full upstream recollection. Each automatic source gets an independent90-second worker budget. Corrupt history is never reset to an empty ledger.
- Manual Haina/Toonflow evidence is retained and automatic login skipped. Other eligible pricing decisions continue independently; tiered contracts are not falsely updated by a ratio-only updater.

## After cutover

- Native image: `new-api-fixed:issue183-runtime`, immutable ID prefix `8ac4d2a3590c`.
- Gateway image: `xtai/image-jobs:issue183-audit`, immutable ID prefix `7d73d65b0b7e`.
- Database authority true, batch updates false, pool30/5/300, two required native networks intact.
- Existing ordinary user key verified through both native and public `/v1/models`: GPT6.1,Pro,Flash,GPT6 andClaude remained accessible under existing ACLs.
- Public pricing carries the exact tiered expression and Pro group0.15 x internal0.8 = CNY0.12.
- Public admin channel/user APIs reject unauthenticated access401; health endpoint remains intentionally readable.
- Read-only patrol:20 healthy,0 failed,0 unknown;1 honest existing partial-pricing warning (0 applied /2 unchanged). A worker completion is not claimed as a price write.
- Fresh live collector:7 complete,0 incomplete,2 manual skips,17.75s. Historical ledger preserved; compact health artifact208 bytes.
- Review artifact posted to issue183; follow-up184 tracks the pre-existing URL trust/SSRF boundary without claiming a demonstrated exploit.

## Historical uncertainty: not falsely closed

`ijob_e21e1fb6e8574a7c881d472084bff7de` (Sep8) and `ijob_0615d70c29b549458e5f198f0ed1c301` (Oct1) still lack original payload/native request IDs needed to join upstream generation and fees. The old retention process had removed their payloads; available recovery archives contain configuration but no original matching gateway data. A neighboring Oct1 job has a different fingerprint and cannot be used as their evidence. Both are preserved as uncertain; no replay, invented result, user-wallet mutation or guessed supplier refund.

Private backups and exact billing/API evidence remain under server `/opt/ai-api-stack/backups/issue183-runtime-20261006` with restrictive permissions. No secrets are committed or included in this report.
