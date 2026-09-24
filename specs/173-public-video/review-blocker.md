# Issue173 safety review — NOT APPROVED for deployment

2026-09-24. Independent comprehensive/security review completed on the prototype, not production.

Critical unresolved: standalone wallet delta delivery cannot coordinate native batch writes and cache rehydration. A cached balance can be debited twice or a spent amount can reappear in cache. Native DB-only precheck may accept funds already used but not flushed by the main service. No uncoordinated direct-SQL/DEL/HINCRBY workaround is approved.

Other review findings addressed in prototype: pending_review remains pollable and can settle on later proof; state writes use compare-and-swap so settled rows are not reopened; new reservations preflight model availability and exact gateway price, while existing request replays do not need a new price quote; malformed catalogs return503. Broader concurrency/restart/backoff/readiness tests still required before release.

Verification: `go test ./model ./cmd/public-video -run '^TestPublicVideo' -count=1` passed seven focused tests. This covers atomic reserve/replay/conflict, refund/supplement, amount bounds, per-user read/download isolation, auth/IP/model constraints, simulated process reconstruction, and same-event cache receipt replay. It does not cover the unresolved native cache epoch race.

Existing production remains unchanged: Nody seven videos via shared service key; public ordinary-key access not enabled. No user balance mutation or paid generation. This prototype has a default startup safety block. No PR should be created with a COMPLETE review claim.

Await user decision to upgrade native accounting/auth integration or defer public access. Work is retained on codex/issue-173-public-video; issue173 tracks acceptance and outstanding safety work.
