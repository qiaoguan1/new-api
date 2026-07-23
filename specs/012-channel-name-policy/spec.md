# Feature Specification: Canonical Channel Names

**Issue**: [#12](https://github.com/qiaoguan1/new-api/issues/12)

## User Outcome

Every channel is recognizable at a glance and future operators have one naming
rule: `上游名 · 用途`.

## Acceptance Criteria

- All 35 current channel IDs have one explicit legacy-to-canonical mapping.
- Canonical names are unique and contain exactly one ` · ` separator.
- The migration rejects missing, additional, or manually drifted channels.
- One guarded transaction changes only `channels.name`; every other channel
  field has the same database fingerprint before and after.
- A mode-0600 JSON backup is written before the transaction.
- PackAPI and Unity2 remain disabled and absent from active upstream credentials.
- The integrated and standalone Channel Monitor guides document the rule.
- Daily channel audit remains 11/11 healthy after the rename.

## Non-Goals

- Do not infer names automatically from arbitrary future model lists.
- Do not change channel IDs, keys, models, groups, weights, priorities, or status.
- Do not re-enable retired channels or delete historical records.
