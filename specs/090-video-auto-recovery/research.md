# Research

## Current behavior

- Definitive pre-creation failures advance the persisted route.
- Ambiguous submits become `uncertain`.
- Once an upstream task ID exists, polling failures never switch routes.
- Provider final failure is terminal.
- The current settlement table assumes one provider task per downstream job.

## Available evidence

- Paisio exposes authenticated `/api/task/self` plus `/api/log/self`. Monetary truth is the signed request ledger, not task quota.
- Toonflow exposes authenticated operation logs filtered by `taskICode`; the terminal row contains exact price.
- Production credentials are already mounted read-only from restricted server storage and must be reused without copying values.

## Key decision

"AI automatic judgment" is implemented as a deterministic evidence engine. An LLM cannot be authoritative for money or task creation and would add data leakage and nondeterminism.

## Important limitation

Neither upstream has yet been proven to expose an authoritative "no task exists for this client idempotency key" endpoint. Therefore an ambiguous submit cannot immediately switch merely because a first lookup is empty. It must remain in automatic reconciliation until the bounded evidence window expires or a provider-specific authoritative absence condition is established.

