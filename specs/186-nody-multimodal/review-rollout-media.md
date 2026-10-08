<!-- REVIEW:START -->
## Code Review Complete — fresh media rollout preparation

| Property | Value |
|----------|-------|
| Worker | /root |
| Issue | #186 / PR188 |
| Scope | MAJOR operations helper |
| Security-Sensitive | YES |
| Reviewed | 2026-10-08 |

### Criteria results

| Criterion | Status | Result |
|-----------|--------|--------|
| Blindspots | FIXED | Fresh operation/state/names; immutable identities; queue guard before stop; partial rename recovery |
| Clarity | PASS | No hidden paid test, pricing-policy selection, old-journal reuse or database restoration |
| Maintainability | PASS | Import-safe host adapter and pure simulated release tests |
| Security | FIXED | Exact scope for targets/secret paths/config; private artifacts; source/profile attestation; preserve normal-key authentication |
| Performance | FIXED | Bounded detached900s runner with600s emergency-recovery grace; bounded local reads/backups/HTTP/Docker actions |
| Documentation | PASS | Preparation-only state and missing financial decision explicit in expansion.md |
| Style | PASS | Relevant tests/diff checks; no shell interpolation, production SQL write or destructive database operation |

### Findings fixed

1. Previous helper reused an already-promoted operation and fixed names: require a new32-hex operation and unique journal/rollback names.
2. Old media/canary assumptions excluded AV and required paid proof: new helper only consumes explicitly reviewed profiles/retail expectations and performs free discovery; it does not invent a billing policy.
3. Authorized descriptions, collection order and version-hash placement caused false drift: normalize only known unordered metadata and the exact additive description suffix; preserve money and pricing revisions.
4. Sorting arbitrary lists could hide ordered frame drift: unknown lists remain ordered, with promote/rollback reversal regressions.
5. Promotion exceptions could strand stopped services: one journal-owned rollback preserves current data and refuses to interrupt new active media jobs.
6. Runner termination could bypass recovery: TERM enters the same single recovery attempt, with bounded hard-kill grace and protected error/recovery journals.
7. New media profile drift could unnecessarily block old-code recovery: rollback still protects existing image/native identities while allowing recovery from corrupt new-profile content.

### Verification boundary

19 pure operator tests pass. Full gateway regression:276 passing tests. Tests simulate
the host adapter; they do not call Docker, upstream generation, production SQL,
SSH or real wallet operations. The helper has NOT been executed against production.
No additional-mode live acceptance or new pricing policy is claimed verified.

**Unaddressed: 0**
**Review Status: COMPLETE** for release preparation; activation awaits the user's financial choice.
<!-- REVIEW:END -->
