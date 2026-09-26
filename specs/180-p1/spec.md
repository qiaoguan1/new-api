# P1 repair: automatic key grouping and truthful independent pricing

User explicitly approved both audit P1 repairs on2026-09-26. This does not authorize customer balance changes, video changes, abandoned Jojo restoration, guessing costs, or relaxing public upstream HTTPS safety.

## Acceptance

1. Normalize newly created omitted/blank API token group to auto at a shared persistence boundary; explicit nonblank groups and all other token properties remain unchanged.
2. Back up and migrate active nondeleted existing blank tokens only. No key rotation, balance changes, whitelist/IP changes or explicit-group migration.
3. Exact trusted internal adapters can be inspected without making arbitrary private HTTP addresses acceptable. Preserve SSRF/HTTPS protections for public/untrusted providers.
4. Daily repricing evaluates independently per model with complete retained-route cost coverage, exact model/resolution/cache units and current max-cost×1.5 rule. Manual-weekly Haina/Toonflow evidence remains manual; absent evidence must never become invented price or zero cost.
5. Health/reporting separates execution success from actual writes/no-change/partial/blocked; zero writes with unresolved cost failures cannot appear healthy. One upstream failure cannot abort others or prevent digest generation.

## Tasks

- [x] Trace exact production source, collect backup and establish failing regression tests.
- [x] Implement cross-database token creation normalization and isolated tests.
- [x] Repair adapter/cost-source integration and business health semantics with deterministic fixtures.
- [x] Review security, preserve production pool30/5/300 and quota authority; deploy scoped patches after dry run.
- [x] Migrate62blank keys and verify ordinary catalog, constraints and prices; validate daily job/report result without paid generation.
- [x] Preserve evidence and rollback; remove only own disposable test artifacts; document genuine evidence gaps rather than claim complete pricing. Remaining evidence tracked in#181.
