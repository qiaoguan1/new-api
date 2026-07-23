# Data Model: Consolidate Server Source

## Source Snapshot

Represents one compared source tree.

- `name`: canonical, candidate, or final
- `path`: absolute server path used during reconciliation
- `role`: canonical or comparison-only
- `git_state`: repository/ref/worktree state when applicable
- `inventory`: identical, differing, and exclusive path counts

## Reconciliation Decision

Represents the disposition of a candidate path.

- `path`: repository-relative path
- `classification`: test, implementation, compatibility, dependency, documentation, or artifact
- `source_of_truth`: canonical or final
- `action`: preserve, integrate, edit, or exclude
- `evidence`: comparison or test evidence supporting the action

Validation rules:

- Backup artifacts must always be excluded.
- Every integrated path must have an explicit classification.
- Existing canonical-only migration utilities remain preserved.

## WeChatPayConfig

Configuration used to create and verify WeChat Pay API v3 requests.

- enabled state
- app ID
- merchant ID
- merchant certificate serial number
- API v3 key
- merchant private key path/material
- platform public key ID and path/material
- notification URL and optional descriptive metadata

Validation rules:

- When enabled, every cryptographic identity and key field required for request signing or notification verification must be present and parseable.
- Secrets and key material must never be committed to the repository.

## TopUp Order

Existing top-up record extended to support WeChat Pay native orders.

- internal trade number
- user ID
- requested amount and credited amount
- payment provider/method
- provider transaction identifier
- status and timestamps

State transitions:

```text
pending -> success
pending -> failed/closed
```

Rules:

- Repeated successful callbacks must be idempotent.
- A callback cannot credit a different user/order/amount than the stored order.
- Querying an order must not credit it unless provider status is verified.

## Validation Result

Evidence captured for the consolidation.

- command/check name
- expected result
- actual result
- pass/fail state
- relevant path or test name

The final result must cover focused backend tests, full backend tests, frontend checks, artifact/secret hygiene, and unchanged production container state.
