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
- [x] E008 Review, private backups, detached bounded rollout and public/legacy/access/price verification; document exact open capabilities and remaining external constraints. The later user-approved operator-testing release is live; E007 paid mode acceptance remains separate.

Pricing decision must be explicit: a documented/verified per-output or per-input-second rule may cover a range; a sample alone cannot establish arbitrary modes/specs. Do not retain an old exact-count restriction merely because a field was omitted from prose, and do not declare unsupported model/mode combinations supported merely by changing the directory.

## 2026-10-08 current execution boundary

This subsection is the historical boundary before the later user-approved A
operator-testing decision. The active deployed status is recorded at the end.

The user selected existing-wallet-only Wan/Omni acceptance, with remaining branches implemented first; no recharge was authorized. Wan3 at480p/2s with one image, one2.000000s MP4 and one2.040000s MP3 completed. Its authenticated exact upstream cost was CNY1.200000; the output is a658484-byte MP4 with video/audio tracks and2.020000s container duration. Original intent/receipt/fixture hashes and delivery proof are protected server-local records. The original task was queried, never repeated.

The latest authenticated funded wallet after that test is below the conservative Omni exposure; the exact account balance is kept in protected server evidence rather than repository artifacts. Omni's current public metadata only exposes a0.54–1.6-credit per-second range, not the reference-video720p billing basis. A4s output at the maximum and verified CNY1.5/credit exchange needs conservative exposure CNY9.60. This is an exposure ceiling, not an asserted Omni price. No Omni task was submitted, no estimated tariff was enabled, and no existing-wallet limit was bypassed.

Code includes Wan/Prime frame and mixed reference translation, Omni image/video reference translation, FLUX image/first-last translation and broader Grok image translation. Public admission requires successful exact profiles; empty configuration preserves the deployed text/Grok-image baseline. Profiles bind measured video AND audio seconds, role/count, output spec, aspect and generated-audio flag. Input metadata is checked against real bytes before reservation; signed URLs are excluded from durable fingerprints. Duplicate or uncertain tasks are not resubmitted.

Production remains the previous successful deployment. This expansion must not be described as fully open or deployed. New rollout requires a reviewed fresh operation/backup path and an explicit decision on partial Wan rollout versus completing Omni acceptance; never reuse the already-promoted prior rollout journal.

## Operator-directed testing deployment — 2026-10-08

The latest explicit user request is: no recharge, deploy all, and the user will
perform the generation tests. This supersedes the agent-run paid acceptance gate
for this requested testing release, but does not establish missing prices or
authorize fabricated successful billing evidence. No agent-paid test is to be
created in this phase. Existing settled evidence, legacy pricing, authentication,
material safety and uncertain-task no-replay rules remain intact.

- [x] O001 Record the explicit waiver of agent-paid acceptance; retain original cumulative test budget, no recharge and no automatic generation.
- [x] O002 Identify the remaining admission gate: missing exact media prices, not just the provider wallet. Request a financial choice rather than silently changing reserve policy.
- [x] O003 If explicitly approved, implement a separate operator-testing estimate contract, marked unverified/estimated; never insert a fake successful profile or upstream UUID. Keep final authenticated actual-cost-times1.5 settlement and existing limits.
- [x] O004 Prepare and review a fresh, immutable-candidate release helper with unique rollback names and fresh native/container identities; never reuse the prior promoted journal. The helper is not executed, and currently accepts only the existing exact-profile policy; any separately approved estimated policy must be reviewed explicitly before activation.
- [x] O005 Deploy only after the chosen billing/admission policy is coherent; free-check discovery, normal-key authorization, material validation and old price preservation. No generation during deployment verification.
- [x] O006 Publish the truthful testing-release scope and retained limitations; retain current-data rollback and audit evidence.

Financial approval was given by the user's explicit A response: a current maximum-rate estimate is not a verified
mode tariff or a guaranteed upper bound, especially for video-input billing.
Actual authenticated cost can differ. No speculative exact sale price, free
generation or disabled wallet reservation is authorized by the request alone.

The new helper `rollout_media.py` has19 simulated operator tests, including
fresh immutable identities, pre-stop queue guards, preservation of current
data/native/old profiles, one scoped failure/TERM recovery, semantic unordered
catalog metadata and strict ordered-frame/money/revision checks. Full gateway
regression now passes276 tests. No host, container, wallet or billing-policy
mutation was performed by these tests or this preparation.

## Approved estimated holds — 2026-10-08

User selected A: open the candidate implementation for manual testing, no agent
paid generation or recharge. Missing-price candidate holds use current production
token group maximum display-credit rate × verifiedCNY1.5 exchange × group factor
× requested output seconds × retail1.5. Holds retain the existingCNY150 cap;
the estimate is not a guaranteed cost upper bound. Final authenticated actual
cost ×1.5 remains authoritative. New estimated-task supplements require available
user/limited-token quota; shortage preserves the original result/bill pending
funding, never resubmits and never makes that new-path wallet negative. Existing
verified pricing, frozen requests and legacy debt semantics remain unchanged.

The actual production token group was freely confirmed as 默认通道, multiplier1,
currency exchange1.5. No key or account balance is exported. New rules are
explicitly `verification_status=unverified`, `pricing_kind=estimated_reservation`;
they do not reuse successful profiles or manufacture UUID evidence.

## Live operator-testing release — 2026-10-08

Operation `2f18e153411d44bf9ea976a0bcf5b622` reached
`promoted_verified`. Both gateway instances and the public video service run the
reviewed immutable candidates; the fresh native application identity is unchanged.
WAL-aware SQLite and PostgreSQL backups, original price snapshots, preserved
rollback containers and private audits remain server-local.

The isolated canary passed seven free wide-text preflights and ordinary/denied
authentication checks with unchanged quota, task and accounting-log counts.
Public HTTPS ordinary-key discovery, capabilities, prices and marketplace return
200 and expose all seven operator rules. The internal compatibility capabilities
and price endpoints also return200 with their existing service authentication;
ordinary user keys continue to use the main public host, not that internal host.
Old seven text tariffs and six Grok exact image tariffs passed strict semantic
preservation checks. This does not certify successful paid generation for every
candidate tuple; the user explicitly owns the remaining manual acceptance.

The first isolated candidate failed because copied source permissions were600;
image packaging now makes only runtime source/data files readable0644. Production
was never switched to that failed candidate. A canary baseline service-key404 was
fixed by using the existing reviewed ordinary-user read-only snapshot. No ACL or
user/key settings were changed. The final Python suite passes318 tests; public Go
and model suites, vet and the Linux build pass. No recharge or agent-created paid
generation occurred in this release.

Both owned canary container pairs and isolated PostgreSQL databases were removed
after zero-task/unchanged-wallet audits; final SQL/SQLite audit backups remain.
The failed startup's absent SQLite was accepted only by the explicit reviewed
OWNER-only/nonzero-exit cleanup path. Local generated build files were moved out
of the checkout to a recoverable task-artifact archive. PR188 remains a draft,
unmerged and without CI checks; live verification is not a claim of CI success.
