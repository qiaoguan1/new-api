# Quickstart: Verify Paisio-First Routing

1. Run the gateway unit and integration tests.
2. Resolve at least 100 request IDs for every shared full/Fast resolution.
3. Confirm every plan is `paisio, toonflow` and Mini is Toonflow-only.
4. Simulate definite Paisio rejection and confirm exactly one Toonflow submission.
5. Simulate uncertain Paisio submission and confirm zero Toonflow submissions.
6. Verify public responses contain no provider or route-plan fields.
7. Back up production source, catalog, environment, image metadata, and SQLite state.
8. Deploy the reviewed commit and validate health without creating a paid canary unless separately authorized.
9. Inspect newly accepted real tasks as they arrive; do not rewrite existing jobs.
10. Run ten read-only rounds checking service health and duplicate request IDs.
