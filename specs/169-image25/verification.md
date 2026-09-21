# Image-2.5 live verification

Date: 2026-09-21, Asia/Shanghai. Issue169.

## Supplier evidence

- Rolldek `gpt-image-2.5`: 1K test succeeded25.51s; ledger quota7500/500000=0.015, group1 and configured conversion1.0.
- Hanhe `gpt-image-2.5`: returned actual PNG pixels1024/2048/4096 square in29.26/20.40/22.14s. Authenticated usage records68644809/68645081/68645084 have actual_cost0.025/0.05/0.10, image_count1, tiers1K/2K/4K; conversion1.0.
- Haina `flare` and `sunburst`: HTTP200 error payloads, no image; zero-quota type5 error logs. Not onboarded. No official model identity inferred.

## Deployment

- New image-only adapter container: `xtai-image25-adapter`, internal8095, non-root/read-only/no published port, cap_dropALL, concurrency2.
- New channels55(Rolldek priority10) and56(Hanhe priority8), only `gpt-image-2.5`, image group.
- Existing channel records were byte-for-byte equivalent after excluding new IDs.
- Backup: `/opt/ai-api-stack/backups/image25-onboard-20260921-135125`, private before snapshot and rollback.sql.
- Additive options: new model in ModelPrice and billing_setting maps; all old option entries preserved. Base tiers0.25/0.5/1.0 × group0.15 =0.0375/0.075/0.15.
- Generic cost collector does not update the tier expression; this new expression requires explicit per-resolution cost review rather than blind flat-price recalculation. Existing image/video configurations are unchanged.

## Verification

- Eight adapter tests pass, including HTTP auth and no-upstream-call non-JSON rejection; invalid result response checks; size gates; post-generation no-replay behavior.
- Full `go test ./pkg/billingexpr ./relay/helper -count=1` passes; dedicated three-tier regression confirms18750/37500/75000 quotas.
- Public `/v1/models` returns new model; `/api/pricing` returns tiered_expr and exact expression.
- Public invalid-size and urlencoded tests rejected, token consumption unchanged. Public NewAPI wraps errors as500; adapters return400/429 before generation. No billing attached to these rejects.
- Public canaries:1K200/29.58s/channel55/quota18750;2K200/24.26s/channel56/quota37500;4K200/30.48s/channel56/quota75000. All returned b64 images. Usage rows66717/66719/66721 confirm frozen tiers1k/2k/4k.
- Operator token402 revoked in finally.

Limits: only single square text-to-image with default auto quality. Other ratios, edits/reference images, multi-image batches and supplier variants not enabled. Current catalog/manual-price display is not a guarantee of supplier official identity or long-term reliability.
