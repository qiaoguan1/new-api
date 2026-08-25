# Research

## Verified facts

- OpenAI Codex serializes standalone search to `alpha/search` relative to the
  provider base URL.
- The official request fields are `id`, `model`, optional `reasoning`, optional
  `input`, optional `commands`, optional `settings`, and optional
  `max_output_tokens`.
- The official response requires a string `output` and optionally carries
  `encrypted_output` and opaque `results`.
- XT's Codex channel currently maps Responses to
  `/backend-api/codex/responses`, and applies the subscription OAuth access
  token and account ID headers in one adaptor.
- Production currently has no `/v1/alpha/search`; the desktop sees search-start
  events and waits until its bounded task timeout.

## Decision

Use a dedicated relay format rather than disguising the request as a Responses
request. This preserves the official wire contract, reuses channel credentials,
keeps billing/refund behavior in the central controller, and avoids an internal
recursive HTTP call.

Production GPT-5.6 channels are generic OpenAI-compatible providers and all
three tested providers reject `web_search_preview`. Current upstream
CLIProxyAPI implements `/v1/alpha/search`, but its real candidate request was
blocked by ChatGPT for the production server egress IP. It cannot be the
production dependency without a separate stable network exit.

An isolated SearXNG candidate returned 20 Chinese and English results per
request. A real adapter integration completed search -> cached reference ->
open on a `gov.cn` page in 2.38 seconds. Some public engines hit CAPTCHA, while
Google CSE remained available; the deployment therefore treats SearXNG as a
replaceable internal aggregator and surfaces backend failures instead of
hanging.

The first version supports `APITypeCodex` and `APITypeAdvancedCustom`. An
Advanced Custom channel is configured with a high-priority, path-restricted
`/v1/alpha/search` route and a SearXNG base URL. The handler translates the
official command envelope and provides bounded search/open/click/find/time
operations. Ordinary `/v1/responses` excludes the channel through the existing
path-aware channel filter. Generic OpenAI-compatible channels remain
fail-closed.

Production currently runs a selectively backported source baseline that
predates Advanced Custom channels. For this deployment only, an explicit
`XT_STANDALONE_SEARCH_BASE_URL` opt-in provides the same SearXNG execution path
without importing unrelated channel infrastructure. The middleware still
applies token model access and pricing, while successful internal search clears
the selected provider channel ID before usage accounting.
