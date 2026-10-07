# Full supported-mode expansion — 2026-10-07

User explicitly requests all upstream-supported modes be connected/opened. The previous deployment remains live: seven text modes and six exact Grok image profiles. This phase must not disable them or mutate unrelated channels, user access/balances, prices, or schedules.

## Primary implementation evidence

Installed vendor code `D:/Nody/resources/backend-dist/main.js` (modified2026-09-22) supplies model-specific v2 video request builders. These are stronger evidence than missing prose, but not live acceptance or current tariffs. Public server status advertises desktopv1.1.6; installed version/equivalence remains unverified.

| Models | Candidate supported inputs/modes | Required wire |
|---|---|---|
| Wan3 / Wan3Prime | images/video/audio; first/last/first-last; reference | `generation_type=frame/reference`, `image_urls`, last-only `image_with_roles`, `video_urls`, `audio_urls`; reference ratio `size`; uppercase resolution; `audio` for generated sound |
| OmniFlash | images/video; reference | `image_urls`, `video_urls`, `aspect_ratio`, lowercase resolution; explicit duration semantics require acceptance |
| FLUX3 | images; first/first-last/multi-reference | `image_urls`, `aspect_ratio`, `audio`, `safety_tolerance=4`; no separate role discriminator in wire |
| Three Grok variants | image references, up to7 | Existing variant-specific fields; wider counts/duration/resolutions need evidence/rule coverage |

Do not infer video/audio rejection for Grok/FLUX from this older client; investigate newer definitions if available. Do not copy old embedded prices.

## Checklist / Spec Kit continuation

- [x] E001 Confirm current deployed baseline and originalCNY50 cumulative budget (already spentCNY4.95); request optional budget clarification, no recharge.
- [x] E002 Read current primary docs, public web metadata and vendor installed request builders. Trace relay guards through read-only code explorer.
- [x] E003 Add RED wire-contract tests, implement explicit model-mode mappings without changing legacy text bytes.
- [x] E004 Reuse/generalize media safety, content identities and first/last semantics; bounded total deadlines and offline decoder protocols.
- [x] E005 Implement authenticated exact mode/count/audio/aspect/input-duration profiles and quote coverage; retain actual-cost-times1.5 settlement and no uncertain replay. No general tariff is inferred from a range or sample.
- [x] E006 Extend public/gateway/catalog/price/preflight/store integration and test exact invariants. New code is not yet deployed.
- [ ] E007 Live acceptance: Wan mixed image/video/audio case accepted with exact receipt and MP4/audio/video delivery proof. Omni remains blocked by unproven current input-versus-output billing basis and insufficient conservative funded exposure. Other branches are code-first per explicit user decision.
- [ ] E008 Review, private backups, detached bounded rollout and public/legacy/access/price verification; document exact open capabilities and remaining external constraints.

Pricing decision must be explicit: a documented/verified per-output or per-input-second rule may cover a range; a sample alone cannot establish arbitrary modes/specs. Do not retain an old exact-count restriction merely because a field was omitted from prose, and do not declare unsupported model/mode combinations supported merely by changing the directory.

## 2026-10-08 current execution boundary

The user selected existing-wallet-only Wan/Omni acceptance, with remaining branches implemented first; no recharge was authorized. Wan3 at480p/2s with one image, one2.000000s MP4 and one2.040000s MP3 completed. Its authenticated exact upstream cost was CNY1.200000; the output is a658484-byte MP4 with video/audio tracks and2.020000s container duration. Original intent/receipt/fixture hashes and delivery proof are protected server-local records. The original task was queried, never repeated.

The latest authenticated funded wallet after that test is below the conservative Omni exposure; the exact account balance is kept in protected server evidence rather than repository artifacts. Omni's current public metadata only exposes a0.54–1.6-credit per-second range, not the reference-video720p billing basis. A4s output at the maximum and verified CNY1.5/credit exchange needs conservative exposure CNY9.60. This is an exposure ceiling, not an asserted Omni price. No Omni task was submitted, no estimated tariff was enabled, and no existing-wallet limit was bypassed.

Code includes Wan/Prime frame and mixed reference translation, Omni image/video reference translation, FLUX image/first-last translation and broader Grok image translation. Public admission requires successful exact profiles; empty configuration preserves the deployed text/Grok-image baseline. Profiles bind measured video AND audio seconds, role/count, output spec, aspect and generated-audio flag. Input metadata is checked against real bytes before reservation; signed URLs are excluded from durable fingerprints. Duplicate or uncertain tasks are not resubmitted.

Production remains the previous successful deployment. This expansion must not be described as fully open or deployed. New rollout requires a reviewed fresh operation/backup path and an explicit decision on partial Wan rollout versus completing Omni acceptance; never reuse the already-promoted prior rollout journal.
