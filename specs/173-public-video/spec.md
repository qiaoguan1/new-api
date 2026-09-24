# Public API-key video access

User authorization: all regular users/API keys can call the new verified models. Preserve disabled/expired/account/IP and user-selected model restrictions; no requirement to create or share a service key. All legitimate user/token groups can use the seven verified Nody video models. Existing Flare/Sunburst image routes will cover legitimate groups without changing price.

## Plan and acceptance checklist

- [x] Inspect production gateway, native authentication, group rules, wallet conversion and caches.
- [ ] Add isolated Go public front door using existing NewAPI model/settings code; never rebuild or migrate the unrelated production main binary/schema.
- [ ] Dedicated durable task and wallet ledger: owner+request idempotency, atomic user/token reservation, exact-cost final delta+consume log. Pending/unknown never refunds without proof.
- [ ] Independent public execution gateway disables downstream-specific webhooks; existing service-token clients proxy unchanged to original gateway.
- [ ] Validate ownership for status/content, model limits, IP, expiry, overflow, replay/conflict, failed refund and restart recovery.
- [ ] Canary with database backup and bounded paid verification; production routing cutover, all legitimate groups image compatibility, public docs and rollback.

No Grok image enablement. No modifications to historical balances/prices, existing upstream routing, unrelated schedules, or disabled accounts. Fixed verified exact specs only. Wallet denomination is frozen per task from site quota-unit settings and face-value Price (currently1CNY per500000quota), not USD display FX7.3 and not upstream credit conversion1.5. Live payment configuration must be verified before deployment.

Public API contracts: POST /v1/videos; GET /v1/videos/{public_id} and /content; GET /v1/capabilities and /v1/video-prices with regular Bearer key. Client request_id/Idempotency-Key optional but when provided must agree. Absent keys get generated IDs; clients should retain IDs and never change them to retry uncertain requests. Shared-service calls retain exact previous semantics.

## Deployment gate: BLOCKED, not production-ready

The standalone prototype was security-reviewed and MUST NOT be deployed with shared native wallets. Native production uses BATCH_UPDATE_ENABLED=true. Its DB quota can lag already-spent cache quota. Applying a sidecar cache delta after DB commit can also double-apply against a newly rehydrated cache; an asynchronous native refresh can overwrite it. A delivery receipt alone does not solve cache-epoch consistency.

The implementation now fails startup unless explicitly marked development-only. No production binary, route, user balance, channel, or price was changed in this turn. No paid test was run. Seven focused tests passed (transaction/idempotency, ownership, strict input, limited-token auth, refund/supplement, process reconstruction); this does NOT establish safety with native concurrent accounting.

Needed decision: A, authorize native main-site wallet/auth integration upgrade with reversible brief service restart; B, keep the existing shared-service downstream route only; C, retain prototype/specification and defer rollout. The user was asked through the task question tool. Do not enable the sidecar as a workaround. After authorization, use verified production main-site source as baseline and coordinate DB/cache accounting within the native workflow.
