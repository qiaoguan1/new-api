# Production verification checkpoint — 2026-10-10

## Outcome and authority

Partial implementation is live. The user authorized model checks and fixes,
with at most CNY20 from existing upstream balances. Exactly one paid generation
POST was made; no recharge, historical replay, customer balance adjustment,
user/key permission relaxation, paused-provider restoration or tariff write was
performed. Jojo remains paused. Haina0809 and Toonflow manual-weekly price-evidence
rules are retained.

## Live changes

- Nody Image2.5, legacy Image2.5 and Banana adapters now separate definite
  pre-submit rejection from uncertain post-submit outcomes. A transport error,
  unreadable result or ambiguous provider failure cannot safely prove no charge.
  The existing gateway/native no-replay boundary receives uncertain HTTP502,
  rather than a misleading definite HTTP400. Exact route/model identity remains.
- Image2.5 generic, Flare, Sunburst, Banana Flash and Banana Pro marketplace metadata advertises
  `image-generation` at `/v1/images/generations`, not chat. Unsupported editing,
  references and sizes are not silently translated.
  Exact new metadata IDs193–197 were read back. Banana is assigned to the existing
  verified Google vendor39 with `sync_official=0`, not treated as an official ID.
  The two Banana entries had no previous exact metadata; existing effective chat
  labels were captured before adding the image-only entries. The transactional
  channel and option fingerprints were unchanged. A fresh public GET confirms
  all five entries are image-generation only; no generation request was required.
- Patrol's read-only video queue reader works within the existing systemd
  sandbox via the verified owner-container fallback. Missing/unreadable evidence
  is unknown, not an invented zero; no filesystem permissions were broadened.
- The official-video pricing worker records dated failures, unchanged actions
  and actual/unknown writes truthfully. Source price math and allowed routes are
  unchanged. Dry-run verification performed zero price writes.
- The daily backup worker and explicit recovery-root configuration are live.
  Bundle `newapi-20261010-183036` is verified `configured_complete`: nine roots,
  18 runtime descriptors and 18 consistent SQLite snapshots. Archive and member
  hashes and SQLite quick checks passed. PostgreSQL dump listing was checked;
  an isolated full restore was NOT performed. This is local, per-store recovery
  coverage, not globally atomic or off-host disaster recovery.

## Model evidence — do not confuse listing, billing and output

Fresh ordinary auto-group key discovery returned HTTP200 and 53 models. All13
core IDs below are visible. `/api/pricing` returned65 entries; its12 additional
Topaz names are not in that key's protocol-filtered model directory. No attempt
was made to enable unverified names merely because the marketplace lists them.
The extra names are `aaa-10`, `ganim-1`, `pnat-1`, `sl-1`, `slc-1`, `slf-1`,
`slf-2`, `slhq-1`, `slm-1`, `slp-2`, `slp-2.5`, `wonder-1`. The filter's precise
provider-support rationale is not yet freshly attested; they are not labeled
paid-output verified, and this repair does not alter video or upscaler admission.

| Model | Evidence this run | Remaining limitation |
| --- | --- | --- |
| gpt-5.5 | Visible;112 native consume records in the fresh seven-day read | Not newly paid-tested |
| gpt-5.6-sol | Visible;169 consume records | Not newly paid-tested |
| gpt-5.6-luna | Visible;two consume records | Not newly paid-tested |
| gpt-5.6-terra | Visible and enabled routes | No recent consume record; not certified by a paid test |
| gpt-6 | Visible and enabled routes | No recent consume record; not certified by a paid test |
| gpt-6-astra | Visible and enabled routes | No recent consume record; not certified by a paid test |
| gpt-6.1-sol | Visible;53 consume records | Not newly paid-tested |
| gpt-image-2 | Visible;217 consume records;25 error records | Intermittent failures exist; no blanket availability guarantee |
| gpt-image-2.5 | Visible;one consume record; six error records | Only supported generation specs; no fresh paid output proof |
| gpt-image-2.5-flare | Visible;one new fully validated PNG1024x1024 | Exact attributable bill still pending; no edit/ref/other-size proof |
| gpt-image-2.5-sunburst | Visible;one consume record;two error records | No second paid test performed |
| banana-flash | Visible;one consume record;18 error records;adapter repair live | Fresh paid generation still unverified;size is prompt-only intent |
| banana-pro | Visible and enabled1K route;adapter repair live | No recent consume record;no new paid output proof |

Native consume rows are billing-path evidence, not proof that every image byte
was delivered or that the provider's currently available balance/spec is valid.
Error counts and consume counts may contain different business attempts and must
not be presented as an exact upstream failure rate.

Claude directory IDs are `claude-fable-5-1`, `claude-haiku-4-5-20251001`,
`claude-sonnet-4-6`, `claude-sonnet-5`, `claude-opus-4-6`, `claude-opus-4-7`,
`claude-opus-4-8`, `claude-opus-5`, `claude-opus-5-5`. Fable has45 recent consume
and12 error records; Opus5.5 has one consume record. The other variants are
directory/route verified only, not newly generation tested.

The seven public video IDs remain visible: `wan3.0-video`,
`wan3.0-video-prime`, `flux-3-video`, `grok-video-3`,
`grok-imagine-1.5-video`, `grok-imagine-video-official`, `omni-flash`.
Their existing `operator_testing=unverified` marker is preserved; this round made
no paid video task and changed no video model configuration. Legacy SD3 and
Topaz directory presence likewise does not certify a fresh output.

## Paid output and unresolved association

Flare response contains a validated full PNG1024x1024,780655bytes,
SHA256 `91de5ea4119446de0830b6e06d3954759f7d412d05121030c06cdc8544841a14`.
Its preflight maximum quote is CNY0.30, not a claim that exact spend is verified.
The provider's authenticated bill lacks the exact returned/request relay linkage.
Same-model, same-time evidence was rejected as insufficient. The native test key
was closed; unknown attribution blocks another paid submission. No Sunburst
intent or second POST exists. Original response/bill evidence remains protected
on the server for exact reconciliation.

## Blocked, safely retained work

The native policy candidate is built from the exact serving source, preserving
the current embedded frontend and production pricing implementation. Its service
tests and vet passed. It adds narrow HTTP402 quota failover and stronger explicit
task-ID/uncertain-result protections. It is NOT deployed.

The old production main uses `server.Run`, without graceful shutdown. The initial
maintenance helper covers13 image/text paths but does not cover all native relay
admissions such as Gemini, Realtime, audio, embeddings, MJ and Suno. Quiet socket
samples alone cannot close those races. Promotion is blocked pending the user's
maintenance choice and an exact reviewed ingress plan; video containers, polling,
settlement and configuration must remain intact. No legacy process was stopped.

The official-video pricing evidence expired2026-10-08. The new worker correctly
retains current prices and records failure; no expiry extension or invented cost
was made. Generic daily repricing's zero apply/three unchanged/55 skip outcome
is not mislabeled as completed price changes. Missing cost/group/cache/resolution
coverage needs actual fresh evidence, not forced writes.

## Validation and handoff

Monitor suite:220 tests (219 pass,one Windows fork-only skip). Backup suite:33 pass.
Adapter suites: Nody14,Banana9,legacy Image2.5 six;existing gateway nine.
Deployment/extraction/paid acceptance/native-plan suites:117 pass (26 native-plan mocks).
Native service tests and vet passed locally and in the exact-source offline build.
Total Python regressions:407 passed,one skipped. Mock tests are not a production
promotion or a CI result. The native plan defaults to blocked without fresh direct
human maintenance approval plus reviewed exact runtime/source and complete ingress
evidence. The Windows orchestration fixture models the Linux owner/mode for only
its temporary gate path; the production root-owned0600 guard remains intact.

Independent findings were fixed or remain explicit blockers. No CI-green, merge,
full-model output, exact attributable spend or complete restoration is claimed.

Final readback confirmed the site HTTP200, original native instance/image still
healthy, three repaired adapters healthy, gateway healthy, video instances
running, no owned image drain remaining and no native rollout journal. The
completed backup was independently verified again. Reviewed source is published
as attached draft PR194, stacked on the existing Nody branch without rewriting
it. Its status-check rollup is empty: no CI result or merge is claimed. Temporary
source audit copies and simulated fixtures are archived outside the checkout;
the deletion attempt was refused by the tool safety policy, so they were retained
recoverably rather than removed by an alternative deletion mechanism.
