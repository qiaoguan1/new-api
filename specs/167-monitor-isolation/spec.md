# Failure-isolated upstream operations

User scope: skip failed Toonflow collection; one provider must not stop unrelated pricing or daily email. Generation routes, video pricing and balances remain unchanged.

Acceptance:
- Catch and sanitize each recharge provider failure, persist successes and explicit unknowns atomically.
- Missing/stale recharge, audit, ledger or pricing sections degrade visibly; successful email alone records delivery.
- Price each model independently. Preserve existing evidence, freshness, change-limit and shared-model cost checks. Never infer unknown costs or zero balances.
- Report blocked/partial business outcomes instead of pretending zero writes means full success.
- Backup production scripts, test, deploy only these scripts, dry-run pricing then execute only validated decisions, deliver one current daily report and verify deduplication.

Tasks: [x] issue/project and production source; [x] regression tests; [x] implementation; [x] review; [x] backup/deploy; [x] live verification. Scope limitation and remaining pricing policy decision are documented in verification.md.
