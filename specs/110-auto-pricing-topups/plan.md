# Plan

1. Snapshot production cron, pricing evidence, provider inventory, credentials
   metadata, and rollback paths without reading secret values into output.
2. Trace pricing gates and upstream recharge/order API shapes.
3. Add failing tests for per-model source isolation, deterministic daily output,
   and recharge transaction classification.
4. Implement the smallest source and scheduler changes; keep all unknown costs
   and recharge totals fail-closed.
5. Run focused/full tests and comprehensive security review.
6. Deploy with backups, execute the daily chain once, and reconcile decisions.
7. Fetch successful upstream recharge records and publish a redacted aggregate.
