# Quickstart: Verify Multi-Provider Video Routing

## Prerequisites

- Python 3.11 or newer
- A temporary directory for the SQLite test database
- No production credentials are required for automated tests

## Automated verification

1. Run the routing, store migration, adapter, and gateway tests.
2. Run the full gateway test suite.
3. Verify source files compile.
4. Verify no credential-like values appear in tracked gateway files.

Expected results:

- Deterministic test requests distribute between Toonflow and Paisio within the specified range.
- A definite pre-creation rejection advances exactly once to the next persisted candidate.
- A timeout or uncertain response becomes uncertain and performs no alternate submission.
- Restart recovery uses the persisted provider and route index.
- Legacy database rows remain readable and retain their provider.
- Public capability and job snapshots contain no provider-routing metadata.

## Production canary

1. Back up the deployed source, environment file, catalog, image identifier, and SQLite database.
2. Authenticate to both provider model catalogs without printing credentials or response bodies.
3. Confirm the five Paisio full/Fast routes and seven Toonflow routes match the reviewed catalog.
4. Build the exact reviewed source and start a dark container against a copied database.
5. Submit synthetic requests with fake adapters in the dark environment; do not create paid tasks.
6. Enable both providers and deploy atomically.
7. Submit a bounded approved canary set and verify provider distribution, idempotency, audio flag,
   polling, result delivery, and route audit records.
8. Run ten read-only rounds checking service health, job invariants, and zero duplicate request IDs.

Rollback restores the prior image, catalog, environment, and database copy. Existing jobs remain
bound to their original provider throughout rollback.
