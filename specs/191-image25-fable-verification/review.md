<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|---|---|
| Issue | #191 |
| Scope | MINOR source patch; scoped API security review |
| Security-Sensitive | YES |
| Reviewed | 2026-10-12, Asia/Shanghai |
| Worker | independent image25_request_id_review plus root self-check |

| Criterion | Status | Findings |
|---|---|---|
| Blindspots | PASS | Success, HTTP errors, malformed body, transfer failure and timeouts covered |
| Clarity | PASS | Native/relay/caller header priority matches existing Nody behavior |
| Maintainability | PASS | Bounded reusable correlation helpers and contract tests |
| Security | PASS | ASCII IDs at most128 characters; no credentials/control text forwarded |
| Performance | PASS | No additional supplier call, retry, redirect or business-state write |
| Documentation | FIXED | Latest conditional/candidate Nody authority recorded |
| Style | PASS | Existing Python style; standard library only; diff check passes |

### Findings fixed

One minor documentation finding omitted the later Nody exact-Fable authority.
Spec/tasks now distinguish conditional cost coverage from the latest backup
candidate test scope. Independent reviewer confirmed this finding resolved.

### Verification

- RED: newly added contracts failed against the old generic adapter.
- GREEN: generic Image2.5 adapter13 tests; existing Nody adapter14 tests.
- Root independently reran generic13 tests and diff check.
- Source hash: `08cd49d1a4d3c4e3e21e9d293e210c1c78480ccb93eca48676602e02964e4abe`.
- Test hash: `5773edc6c472c6991c93d3a31fc4f168af74c4a80e4a9ba45f081cb84f7cfbe7`.

No source-review findings remain deferred. Live source/configuration identity,
rollback and controlled deployment are still release acceptance gates under
#191, not claimed completed. The old serving native process is unchanged.

This patch retains IDs returned by the provider rather than inventing an ID
from client input. A returned ID is correlation evidence, not independently
authenticated billing proof, model availability or permanent stability proof.

**Unaddressed: 0**

**Review Status: COMPLETE - source review only; not deployed acceptance.**
<!-- REVIEW:END -->
