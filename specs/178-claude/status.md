# 2026-09-26 Claude onboarding: deployed and verified

## Current result (supersedes historical blocker below)

User selected A: authorized scoped database connection-pool repair followed by Claude rollout. Pool30open/5idle/300second lifetime persisted in compose override and verified in live container. Native default100idle/1000open conflicted with PostgreSQL100 limit. Main service recreated; DB not restarted; authority flags/image/networks preserved. Compose removed a previously stopped rollback container; it was restored from original snapshot as stopped, without compose ownership labels. No data/image deleted.

Claude8 exact models now available through11 model-isolated channels on Code Plan/Paisio. Maolao excluded for this Claude rollout after Haiku/Opus4.8/nonresponsiveSonnet5stream tests; its pre-existing services not changed. Code Plan/Paisio dedicatedkeys promoted to non-expiring production keys restricted by serverIP and selectedmodels; Maolao testkey disabled. Production staged channels disabled, then verified in-memory runtime prices and exact DB route identities before activation.

Validation:6unit tests passed;13canary billing rows independently recomputed exactly; first8canary requests reconciled including delayed fallback completions; revisedHaiku4.27s andOpus4.8 5.28s; selectedPaisioSonnet5 stream4.35s; CodePlanOpus5 native/messages5.93s. ProductionSonnet5 HTTP200 in4.71s,976quota=0.001952CNY, wallet/log/formula exact. Ordinaryauto key24 listsall8Claude plusbothGPT6 names; publicpricing8rows match allfourtariffdimensions. Existingmodel tariffs outsideClaude set unchanged. No GPT6/defaultgroup changes.

Final database snapshot:6idle clients,1active diagnostic,5background;0new too-many-clients errors since19:02CST. Canarycontainer andclonedDB removed aftersettlement; ownproductiontestaccount soft-deleted/disabled, remaining syntheticquota zeroed, tokenrevoked verified401. Task-owned/tmp copies removed; source and rollback helper preserved in /opt/ai-api-stack/releases/issue178-claude. Private evidence/backup remains /opt/ai-api-stack/backups/claude178-20260926.

Known boundary: blank/default newtokens not migrated without separateapproval; Claude is available to eligible auto/text groups, not a bypass ofstatus/quota/IP/modelallowlists. Upstreamlabels are notofficialauthenticityclaims. CodePlan mayadd4k–7k upstream inputcontext and exceed requestedmax_tokens in tinyprobes, disclosed in接入说明.md. No official-identity guarantee or allformats/tools validation claimed.

## Historical stage: blocked before A authorization

## Completed

- GPT6 routes: Code Plan channel39 priority10, Haina channel6 priority5; gpt-6 maps to gpt-6-astra. Ordinary auto and empty-group token catalogs both expose these names. Existing account status, quota, IP and model allowlist gates remain enforced.
- Nineteen newly created empty-group keys were found. Empty inherits default (ratio fallback1), while auto routes text at0.15. This is a verified configuration/code discrepancy, not a measured customer overcharge. User decision requested; not changed.
- Direct upstream dedicated-key probes: Code Plan8/8, Paisio5/6 (Haiku404 unsupported), Maolao4/4. Total18 calls,17 successes. Actual bill cost approximately0.11221914 CNY, no recharge. At this historical stage test keys were IP-bound, model-restricted, finite budget at most1CNY each, and expired the following day. Credentials/evidence remain server-private.
- Eight exact model plans computed from actual billing rate metadata, with maximum two providers per model; independent input/output/cache dimensions marked up1.5. Unit tests3 passed.

## Not deployed

No production Claude channels, models or price options were written. No production canary account, cloned database, container or database dump was created. Preparation failed at its first read-only database existence query.

2026-09-26 ~18:24–18:26 CST: PostgreSQL repeatedly rejected connections with `FATAL: sorry, too many clients already`. A retry briefly succeeded (5 background,2 idle,1 active), then exhaustion recurred. Container process snapshot showed99 idle connections from172.19.0.5 and1 from172.19.0.6. Docker inspection maps these to ai-api-stack-new-api-1 and xtai-public-video-catalog176 respectively. PostgreSQL has not restarted (RestartCount0). Public /api/status and /api/pricing returned200 in this interval; this does not prove DB-backed requests healthy.

Do not execute deploy_claude.py apply against production: deployment helper is an unreviewed candidate and downstream billing/interface tests are not run. Existing test keys expire tomorrow and must be deliberately renewed/replaced before any deployment. Preserve source-specific probes; do not replay uncertain calls.

## Historical decision (A selected and completed)

A. Authorize scoped production database connection-pool diagnosis/fix, verify stability, then resume Claude deployment.
B. Continue off-production compatibility work only; wait for production recovery before writes.
C. Deliver verified upstream list and defer Claude rollout.

The empty-group migration/default-creation question is separate; no authorization inferred from Claude enablement.

## Historical resume checklist (completed above)

1. Address production blocker within confirmed scope; cap canary DB pool before starting it.
2. Review deployment helper, add focused tests and rollback before writes. Preserve absent CreateCacheRatio defaults and concurrent price changes.
3. Run isolated native8-model billing tests; validate cache semantics and stream/native formats separately.
4. Promote only verified dedicated keys without transient expiry; stage native rates before routing is exposed.
5. Verify ordinary-key catalog, model ACLs, correct group billing and wallet/log deltas. Revoke canary tokens/accounts and remove task-owned temporary runtime artifacts.
6. Complete7-criteria review, publish review artifact, commit and PR. No PR created yet.
