# Plan

1. Freeze the official SearchRequest/SearchResponse contract from the OpenAI
   Codex source and add DTO/validation tests.
2. Add a dedicated relay format and mode, then register the authenticated
   `/v1/alpha/search` route.
3. Preserve native Codex forwarding and add an Advanced Custom execution path
   backed by an internal SearXNG service.
4. Translate search/image/open/click/find/time commands, protect page fetching
   against SSRF and resource exhaustion, and settle through existing billing.
5. Run focused and full tests, review the diff, and merge through CI.
6. Deploy SearXNG plus the route-restricted Advanced Custom channel and relay
   image with rollback artifacts; then rerun real XT desktop research scenarios.
