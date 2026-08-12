# Review: Paisio Settlement, Isolated Pricing, and Daily Operations Digest

**Reviewed**: 2026-08-12  
**Scope**: Major, security-sensitive

## Results

- **Blindspots**: Fixed exact-filter completeness checks for both Paisio task and billing queries. A provider that ignores `task_id` or `request_id`, returns a short page, duplicates a row, or omits a terminal marker now fails closed.
- **Clarity**: Task-count quota and request-ledger money are explicitly separated in names, comments, fixtures, and evidence source identifiers.
- **Maintainability**: The existing NewAPI task collector and model-scoped pricing planner are extended without adding a new service or SMTP credential copy.
- **Security**: The digest route remains under `RootAuth` and `CriticalRateLimit`, caps bodies at 64 KiB, uses a fixed server-side recipient, validates bounded structured fields, escapes HTML, and rejects status/value inconsistencies. Token files reject symlinks and non-0600 permissions. Provider errors are converted to sanitized codes.
- **Performance**: Paisio performs two bounded request-scoped reads per completed job. Daily aggregation remains linear in existing ledger rows and channels.
- **Documentation**: Spec Kit artifacts and the channel-monitor runbook describe the settlement evidence, isolation behavior, retry schedule, preview, and privacy boundary.
- **Style**: Python compilation, `gofmt`, `go vet`, relevant Go tests, 197 channel-monitor tests, 70 gateway tests, and diff checks pass.

## Findings resolved

1. **Major**: The first implementation did not prove the provider actually honored exact task/log filters. Added `total/items/exact-id` singleton/completeness validation.
2. **Major**: A malformed billing-row `type` could escape as a raw conversion exception. Converted it to a sanitized fail-closed billing error.
3. **Major**: Digest status and nullable monetary values could disagree. Added consistency checks so unknown is never represented as a numeric zero.
4. **Minor**: An unexpected local digest exception could print a traceback. The command now emits only a sanitized `digest_failed` result and preserves retry state.

Unaddressed findings: 0.

