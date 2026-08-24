# Issue 137 Upstream Status Report

**Observed**: 2026-08-25 00:57-01:00 Asia/Shanghai
**Method**: authenticated model catalogs, read-only balance endpoints, credential lifecycle dry-run,
gateway readiness, and existing database logs. No paid inference or media generation was submitted.

## Overall status

- 34 configured channels: 12 enabled, 22 disabled.
- All 12 enabled channels returned a usable authenticated catalog response (HTTP 200); zero catalog
  probes failed and zero critical alerts were generated.
- Balance monitoring covered nine credentialed accounts: seven readable, two unknown.
- All three video gateway instances returned health/readiness 200. Production and staging each had
  two configured providers; the legacy gateway had one. Every gateway was accepting, not draining,
  and had a closed circuit with no active jobs.

Catalog success proves authentication and advertised-model availability. It does not prove that a
fresh paid inference would succeed; runtime history below is used as a no-charge second signal.

## Enabled upstreams

| Upstream | Enabled channels | Catalog evidence | Balance/auth evidence | Runtime evidence and classification |
| --- | --- | --- | --- | --- |
| 海纳 (`0809`) | #6 text, #37 image | Both 200; 4/4 text and 1/1 image models present | Balance unknown: classic login 409 and v1 login 404 | 39/39 successes in 7 days; no calls in rolling 24h. **Available, billing visibility degraded.** |
| Maolao | #23 image | 200; 1/1 model present | USD 103.213370 | 124/127 successes in 7 days; no calls in rolling 24h. Pricing metadata endpoint says API access is only under `/v1/`. **Available, pricing metadata degraded.** |
| JojoCode | #30 text, #32 image | Both 200; all 5 configured models present | Balance unknown: classic login 409 and v1 login 404 | Rolling 24h: 107/107 successes. **Available, billing visibility degraded.** |
| Code Plan | #38 image, #39 text | Both 200; both configured models present | USD 77.337388 | Rolling 24h: 57/59 successes. **Available.** |
| Paisio | #42 video, #44 Banana image | Both 200; image 2/2 present; video 8/12 present | USD 69.686962; scheduled login refresh succeeded | Gateways ready. Four configured video names are absent from the catalog. **Available with a four-model route risk.** |
| Topaz | #43 video upscale | 200; 31/31 models present | No supported balance adapter | Pricing endpoint 404; no rolling-24h calls. **Catalog available, pricing evidence unavailable.** |
| 寒鹤 | #48 text, #49 image | Both 200; all 5 configured models present | USD 52.51148873 | No rolling-24h calls; pricing endpoint 404. **Catalog available, pricing metadata degraded.** |

Paisio models configured locally but absent from the authenticated upstream catalog:

- `sd4-pro8-720p`
- `sd4-fast2-720p`
- `seedance-2.5-480p`
- `seedance-2.5-720p`

No route was changed automatically because the missing names may be local aliases or temporarily
unadvertised models. A paid task or provider documentation would be needed to distinguish those cases.

## Disabled channels and monitored reserves

The following 22 channels were disabled and therefore intentionally not live-probed through their
channel credentials: #1, #2, #3, #4, #5, #11, #12, #15, #18, #20, #21, #22, #27, #28, #29, #31,
#40, #41, #45, #46, #47, and #50.

Some disabled providers remain in the account monitor. Their read-only evidence was:

- iCreat: USD 0.037826.
- NodyHub: USD 0.012172.
- Toonflow: USD 49.716020; CAPTCHA-bound token ready, expiring at Unix `1788942343`
  (about 15.6 days remaining at inspection time).
- Rolldek: scheduled read-only login refresh succeeded; no supported balance result was present in
  the nine-target balance monitor.

The very low iCreat and NodyHub balances do not affect production while their channels remain disabled.

## Non-critical alerts

The daily audit emitted eight warnings and no critical alerts:

- Four pricing-metadata warnings: Maolao image, Topaz, 寒鹤 text, and 寒鹤 image.
- Four actual-cost-unavailable warnings: JojoCode text/image and Code Plan text/image.

These warnings limit cost/margin observability. They do not contradict the current catalog and runtime
availability evidence. No price, credential, channel status, model mapping, or route was modified by
this audit.
