# Funded core-model routing — deployed 2026-09-19

## Effective order (first is primary)

| Model | Routes |
|---|---|
| GPT-5.5 | Code Plan → Hanhe |
| GPT-5.6 Sol | Code Plan → Hanhe → Haina |
| GPT-5.6 Terra | Code Plan → Haina |
| GPT-5.6 Luna | Haina |
| GPT-Image-2 | Maolao → Code Plan → Toonflow1K → Paisio |
| Banana Flash | Existing Haina chat-image adapter |

All enabled suppliers have either current authenticated balance evidence, operator-confirmed recharge plus successful paid calls, or authenticated browser balance evidence. Jojo remains paused; its account login is session-limited and no recharge has been confirmed.

## Cost evidence

Do not compare cache-heavy blended historical rates as if they were fixed prices. Current request billing parameters give non-cached short-context input/output CNY per million:

- Code Plan GPT5.5/Sol0.75/4.5; Terra0.30/1.80. Logs show group ratio0.15.
- Haina Sol1.25/7.50; Terra0.50/3; Luna0.25/1.50. Uses recorded group5 and operator-approved recharge conversion0.05.
- Hanhe Sol1/6 from actual_cost billing data; GPT5.5's last confirmed rate1/6 is historical, not a newly observed price.
- Paisio text GPT5.5 costs5/30, Sol5/40, Terra2.5/20 on this key; current text retail prices do not cover these, so this provider is used for successful Image2 instead of text.
- Image2: Maolao0.0618 from actual records, Code Plan0.10 from this test's50000quota, Paisio0.10 from this test's50000quota. Haina's fresh Image2 record costs0.25, above current retail0.1545; Haina image route removed from eligible pool, while its text routes remain enabled.
- Toonflow authenticated model gallery:1K0.10,2K0.20,4K0.25 CNY/image. Authenticated account balance observed49.72 before tests. Rate source is platform gallery, not an invented per-task invoice.

## Verification

- Code Plan Sol and Terra direct calls succeeded5.29s/1.82s.
- Paisio5.5/Sol/Terra direct calls succeeded; Luna failed502.
- Hanhe5.5 direct call succeeded3.37s; Hanhe Image2 failed502 and remains disabled.
- Code Plan Image2 and Paisio Image2 direct generation succeeded33.84s/35.59s.
- Paisio Banana Flash/Pro preview1 returned503; debit/refund records each net to zero, and the routes remain disabled.
- Toonflow parameters verified against official supplier adapter: https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/vendor/toonflow.ts . Correct body uses size:"1k" and metadata.aspectRatio:"1:1".
- Toonflow async task succeeded. Dedicated adapter1K b64_json dark test succeeded43.54s.2K request returned429 before any submission, allowing a different channel to handle it.
- Public relay final smoke: GPT5.5 2.54s, Sol1.65s, Terra1.29s, Luna2.46s; all HTTP200, complete streams. Short test prompts, not guaranteed production latency.
- Public relay forced-channel54 Image2 returned valid b64_json in45.50s and billed once. Operator test tokens238/239 revoked.
-9 adapter tests passed, including uncertain submission/failed-task no-replay, malformed success, bounds, aspect ratio and resolution checks. Safe download helper reused from previously tested adapter.
- Channel/ability snapshot and rollback: /opt/ai-api-stack/backups/funded-core-20260919-194453 . Video channels42/43/46, GPT6 abilities and retail price options byte-equivalent before/after transaction.
- New service has no published port, no root privileges, read-only filesystem, resource/concurrency bounds and0600 file-backed credentials. HTTP health passed; server has no failed systemd units.

## Explicit limits

Toonflow currently accepts1K single text-generation requests via the unified image route.2K/4K, reference-image/edits, streaming and unsupported quality options are rejected before submission for safe fallback. This preserves current prices and avoids downgrade or below-cost billing. Banana Pro has no newly validated low-cost route and remains unavailable. Account-level ledger collection for some providers still needs login-session recovery; current routing does not pretend those account ledgers are complete.
