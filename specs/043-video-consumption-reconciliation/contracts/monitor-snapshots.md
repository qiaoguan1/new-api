# Contract: Monitor Snapshots

## Private snapshot

`date`, `generated_at`, `providers[]`, `models[]`, and `reconciliation` are allowed. Provider rows
may contain internal provider IDs and actual-cost totals but MUST NOT contain credential material.

## Public snapshot

Only `date`, `generated_at`, and `models[]` are allowed. Each model row may contain stable model,
resolution, availability, task counts and success rate. Keys containing provider/channel, cost,
price, sale, margin, token, credential, username, password or upstream are forbidden recursively.
