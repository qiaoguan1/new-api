# Comprehensive review

## Scope reviewed

- Official standalone SearchRequest/SearchResponse DTOs and validation.
- Relay format/mode/router/controller integration.
- Codex native forwarding and Advanced Custom SearXNG execution.
- Search/open/click/find/time behavior, reference cache, billing, retries, and
  channel-affinity isolation.
- Unit, contract, integration, static, and repository regression evidence.

## 1. Correctness

**Pass.** The route uses the normal bearer-authenticated relay stack, the model
is preserved for access control and billing, explicit zero values survive JSON
round trips, and the official optional fields remain opaque. The production
path is selected through a route-restricted Advanced Custom channel; ordinary
Responses calls cannot select it. Search affinity is neither read nor recorded.

The live adapter completed a Chinese policy search, returned a cached reference,
and opened the referenced `gov.cn` page in 2.38 seconds. Site-originated HTTP
errors such as `openai.com` returning 403 are surfaced immediately instead of
hanging.

## 2. Security and privacy

**Pass.** The route rejects unauthenticated requests. Page opening accepts only
HTTP(S), forbids URL credentials, resolves DNS before access, rejects loopback,
private, link-local, multicast, and unspecified addresses, revalidates every
redirect, and uses a dialer that independently rejects non-public addresses to
reduce DNS-rebinding risk. Search/page response sizes, redirects, query length,
operation count, result count, rendered text, and cache size/TTL are bounded.

Search content is treated as untrusted text; scripts, styles, SVG, canvas, and
noscript content are excluded from HTML extraction. No token, channel key, or
SearXNG secret is returned to the client or committed to the repository.

## 3. Billing and failure handling

**Pass.** The central controller still performs model pricing and pre-consume.
Success settles one accounting token plus the existing per-model web-search
tool surcharge. Search output is not charged again as model output because it
becomes input to the following model turn, where normal Responses billing
applies. Validation/backend/page errors return through the existing refund and
error-log lifecycle and are marked skip-retry so a failed search is not sent to
an incompatible text channel.

## 4. Tests and verification

**Pass with documented repository baseline exclusions.**

- Focused DTO, helper, mode, router, middleware, relay, Codex adaptor, SearXNG,
  SSRF, bounds, HTML extraction, billing metadata, and response tests pass.
- Auth contract confirms missing bearer authentication returns HTTP 401.
- Real SearXNG integration search -> reference -> open passes.
- `go vet` passes for all affected packages.
- `git diff --check` passes.
- Full `go test ./...` passes when excluding only the pre-existing test-state
  failures tracked by Issue #135 and the model-ratio baseline follow-up.
- Race instrumentation was unavailable because the Windows Go runtime has CGO
  disabled; synchronization-sensitive cache code is covered by mutex-based
  implementation and ordinary tests.

## 5. Performance and reliability

**Pass.** Search and page requests have independent deadlines. Bodies are read
through explicit limits. Search result count and open text are bounded, and
references expire after 30 minutes with a 10,000-entry hard ceiling. The
production backend is internal SearXNG; provider CAPTCHAs are observable errors
and do not block the relay indefinitely.

Operational risk: the candidate currently received most results from Google
CSE while several optional engines were CAPTCHA-limited. Deployment therefore
requires health checks and real XT scenario verification before completion.

## 6. Maintainability

**Pass.** Wire DTOs, relay orchestration, and SearXNG execution are separated.
The implementation reuses the existing Advanced Custom path routing rather
than adding another channel type or embedding credentials. Deployment settings
are templated and contain no production secret.

## 7. Documentation and scope

**Pass.** Spec, plan, research, data model, contract, tasks, integration test,
and deployment template are present. The change does not alter existing model
prices, normal Responses routes, desktop packaging, or unrelated repository
test baselines.

## Findings

No unaddressed P0/P1/P2 code finding remains in the reviewed change. Production
activation remains gated on isolated image validation, database/config backup,
authenticated endpoint smoke tests, and real XT desktop research scenarios.
