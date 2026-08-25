# Specification: Codex standalone web search relay

## Goal

Expose the standalone search contract used by Codex app-server through the XT
relay without bypassing authentication, model routing, billing, retries, or
error logging.

## Requirements

1. `POST /v1/alpha/search` uses the existing relay middleware stack:
   performance checks, bearer token authentication, model request rate limits,
   and channel distribution.
2. The request accepts the upstream-compatible fields `id`, `model`,
   `reasoning`, `input`, `commands`, `settings`, and `max_output_tokens`.
   `id` and `model` are required and `max_output_tokens` is bounded by the same
   overflow guard used by Responses requests.
3. The first implementation is fail-closed to either a Codex subscription
   channel or an Advanced Custom channel that explicitly matches
   `/v1/alpha/search`. A Codex channel forwards to
   `/backend-api/codex/alpha/search`; Advanced Custom uses its internal SearXNG
   base URL and the relay translates Codex commands to bounded SearXNG/page
   operations.
4. A successful upstream response must contain a string `output`. Optional
   `encrypted_output` and opaque `results` are preserved byte-for-byte at the
   JSON value level.
5. Success uses locally estimated prompt/completion tokens and the existing
   `PostTextConsumeQuota` settlement path. Upstream, conversion, validation, or
   response failures use the controller's existing billing refund path.
6. The route is non-streaming and never exposes channel credentials.
7. Existing `/v1/responses` and `/v1/responses/compact` behavior remains
   unchanged.
8. Page opening rejects private, loopback, link-local, multicast, credentialed,
   and non-HTTP(S) targets; redirects are revalidated and response sizes are
   bounded.
9. Search query count, total operation count, query length, result count, page
   text, redirects, and in-memory reference cache are bounded.
10. A deployment may explicitly set `XT_STANDALONE_SEARCH_BASE_URL` to use an
    internal SearXNG service without requiring the Advanced Custom channel
    infrastructure. When used, successful search accounting is not credited to
    the model channel selected only for access-control and price lookup.

## Non-goals

- Implementing a new search engine.
- Supporting generic OpenAI-compatible channels that do not expose standalone
  search.
- Changing model prices or billing expressions.
- Packaging a desktop installer.

## Acceptance

- Focused DTO, route, relay-helper, URL, response, and billing tests pass.
- The full Go test suite passes.
- A controlled production deployment returns a valid standalone search
  response through an authenticated XT token.
- The three XT production research scenarios complete without search timeout.
