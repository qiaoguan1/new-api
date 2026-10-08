# Provider contract research

2026-10-07. Primary source: Nody homepage's usage-document link to
https://lcn3ojq2jq5t.feishu.cn/wiki/BdVJwkbWPiUSggkyHoicSXoknfc
and its `NODYHUB_API_DOC.md` attachment (V1.0.0, displayed update 2026-09-23).
The browser preview and authenticated attachment GET were readable. Initial
download actions opened a preview / failed cross-frame attribution; no repeated
blind download or credential extraction was used. Full vendor document is not
republished in this repository.

## Confirmed wire contracts (not yet live/cost acceptance)

All three Grok image-reference variants submit to `/v2/videos/generations`.

| Model | Image field | Single image aspect field | Multiple reference aspect field |
|---|---|---|---|
| grok-video-3 | `images`: URL strings | omitted | `ratio` |
| grok-imagine-1.5-video | `image_urls`: URL strings | omitted | `size` |
| grok-imagine-video-official | single `image: {url}`; multiple `reference_images: [{url}]` | omitted | `aspect_ratio` |

- Provider maximum is seven image references. Production must publish only the
  image counts and output tuples whose result and cost have been verified.
- Grok Video3 uses `resolution=720P`, `duration` for its documented v2 image path.
  Its existing tested text path `/v1/videos` with `seconds`/`size` stays unchanged.
- Imagine1.5 image examples use `quality=720p`; this is distinct from the existing
  tested legacy text resolution480p. Do not silently substitute these specs.
- Official variant documents resolution480p/720p and durations1-15; minimum
  existing known specimen is480p/1sec. Broader tuples still need acceptance.
- These are image references, not documented explicit first/last-frame fields.
  Do not advertise first/last-frame control merely because two images are sent.
- The attachment has no Wan3, FLUX3 or OmniFlash multimodal section. Those models
  retain text-only until a primary contract is established.
- Video/audio reference sections for other models (Seedance/MiniMax) must not be
  copied into these seven model contracts.

## Current baseline and pricing boundary

Existing public capabilities confirm all seven currently text-only with zero
assets. `/v1/models` provider response includes five of the requested IDs;
public `/api/pricing` includes all seven. Neither response proves multimodal
execution or provides an exact mode/image-count price. Display quota is not
automatically CNY. New actual-task billing proof is required before admission.

## Implementation safeguards

Public preflight must verify assets and enabled mode BEFORE public wallet
reservation. Public fingerprint preserves historical text canonicalization and
uses verified ordered content identities for new image requests. Request mode
must not be inferred from image count. A failed image probe must create no
gateway/provider task and no debit; uncertain submits remain non-replayable.

New wire regressions initially failed with six expected text-only adapter
rejections. After the adapter-only patch, all15 Nody-related tests passed.
Gateway admission and production capabilities remain unchanged at this point.
