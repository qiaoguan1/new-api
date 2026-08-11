# Verification Report: Unified XingTu Downstream Video Contract

**Date**: 2026-08-11  
**Issue**: #52  
**Security-sensitive**: YES (authentication middleware, public API, billing, and result delivery)

## Acceptance Criteria

| Criterion | Result | Evidence |
|---|---|---|
| Stable versioned submit/query/error DTO | PASS | Focused Go contract tests and seven valid JSON examples in the deployment guide |
| Durable same-user idempotency before side effects | PASS | Claim, replay, changed-payload conflict, completion, and failure-state tests |
| Ark-official x1.5 reservation | PASS | Official SKU/rate tests, unsupported model/resolution/duration fail-closed tests |
| Actual-cost x1.5 final settlement exposure | PASS | Canonical billing serialization tests for charged/refund/supplement amounts |
| Result withheld until settlement | PASS | Pending-settlement and ready-result tests |
| Provider and internal data redaction | PASS | Submit/query/error/proxy tests and runtime diff inspection |
| Explicit audio/aspect forwarding | PASS | Request normalization tests preserve `generate_audio=false` |
| Legacy and non-video behavior retained | PASS | All 85 non-root Go packages pass |
| Downstream deployment handoff complete | PASS | `docs/xingtu-video-api-v2.md` includes configuration, persistence, fields, examples, errors, retries, debt, privacy, and go-live checklist |

## Commands and Results

- `go test ./controller ./model ./relay ./middleware ./service -count=1`: PASS.
- All 85 non-root Go packages with `go test ... -count=1`: PASS.
- Root Go package compile check with temporary ignored embed placeholders: PASS; placeholders removed afterward.
- `go vet ./controller ./model ./relay ./relay/common ./middleware ./service`: PASS.
- Deployment-guide JSON example parsing with PowerShell `ConvertFrom-Json`: 7/7 PASS.
- `git diff --check`: PASS.
- Ten final deterministic rounds across controller/model/relay/common/middleware/service contract tests: 10/10 PASS.

The clean source-only worktree does not contain the existing generated `web/default/dist` and
`web/classic/dist` inputs, so a direct clean-tree `go test ./...` stops at those `go:embed` directives.
The root package compiled successfully with temporary ignored placeholders, which were then removed;
all 85 backend packages passed independently. No paid provider request was made during verification.

## Review Findings Resolved

1. A pre-provider origin-resolution failure could leave an idempotency claim in `claimed`; it now
   persists a safe terminal failure immediately.
2. The provider realtime-fetch path is ordered after the v2 canonical response path, ensuring the
   public contract cannot be bypassed.
3. Provider-specific errors are mapped to stable public codes/messages.
4. Public result delivery uses an authenticated XingTu proxy URL instead of an upstream URL.
5. V2 result proxying uses private no-store caching and a minimal response-header allowlist.
6. Only exact public stable video SKUs can enter the contract; raw aliases fail closed.
7. Request IDs require lowercase ASCII, matching portable database uniqueness behavior.
8. The contract version now uses the billing service's single constant source.

**Deferred findings**: 0  
**Unaddressed findings**: 0
