# Data Model: Selective Upstream Updates

## Upstream Range

- `source_repository`: immutable public upstream repository identity
- `merge_base`: full commit SHA shared by the fork and upstream
- `head`: full upstream commit SHA frozen for the audit
- `commit_count`: expected number of commits, fixed at 97
- `fork_baseline`: full fork commit SHA used for compatibility checks

## Compatibility Entry

- `ordinal`: unique sequence from 1 through 97
- `commit`: immutable upstream commit SHA
- `summary`: upstream commit subject
- `subsystem`: primary affected area
- `disposition`: one of `adopt`, `manual-port`, `already-equivalent`, `defer`, or
  `reject-with-conflict`
- `rationale`: concise compatibility or conflict decision

### Validation

- Every commit in the range appears exactly once.
- Ordinals are contiguous and unique.
- Commit SHAs are unique and resolve inside the frozen range.
- Dispositions use only the five allowed values.
- `adopt` and `manual-port` entries name their verification evidence in research or tasks.

## Upgrade Batch

- `entries`: ordered adopted or manually ported compatibility entries
- `tests`: focused and regression suites required for the batch
- `rollback_boundary`: repository commit or PR revert; no data migration
- `excluded_operations`: production deploys, paid requests, credentials, production writes

## Protected Invariant

- `name`: routing, pricing, monitoring, video settlement, model mapping, authentication, or operations
- `evidence`: existing code, tests, deployment records, and project conventions
- `overlap_result`: unchanged, separately authorized, or rejected
