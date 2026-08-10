# XingTu video job gateway

This sidecar owns durable video submission between the XingTu cloud relay and reviewed video
upstreams. New API does not select these routes. The checked-in catalog is authoritative for stable
downstream model names and approved upstream model mappings.

## Routing contract

- `seedance-2.0` and `seedance-2.0-fast` use Paisio first and retain Toonflow as the fallback when
  both are configured, healthy, and compatible with the request.
- Provider precedence comes from the reviewed catalog priority (`paisio=10`, `toonflow=20`) and is
  independent of request ID or catalog input order. Repeating the same request ID retains the same
  persisted plan.
- `seedance-2.0-mini` remains Toonflow-only until another route has reviewed capability evidence.
- The complete ordered plan is written to SQLite before any upstream submission.
- Fallback is allowed only after a definite submission rejection with no upstream task ID.
- Timeouts, interrupted connections, malformed success responses, and any other uncertain outcome
  never cross providers. They stop as `uncertain` for manual reconciliation.
- Public capability and job responses omit provider IDs, upstream model names, route plans, costs,
  margins, and credentials.

Provider catalog/list-price data is comparison evidence only. It is not actual deduction evidence
and is not used as the downstream selling price. Actual upstream cost reconciliation is maintained
by the channel-monitor workflow with source timestamps and billing-unit evidence.

Every accepted request freezes the reviewed Ark official quote multiplied by 1.5. A successful
result remains `settlement_pending` and is withheld until the authenticated settlement endpoint
receives an exact task-level provider ledger record. The final charge is that actual net cost
multiplied by 1.5; deterministic revisions make retries idempotent and support later reversals.

## Local verification

From the repository root:

```powershell
python -m unittest discover -s ops/video-job-gateway/tests -p 'test_*.py'
Get-ChildItem -LiteralPath 'ops/video-job-gateway' -Filter '*.py' |
  ForEach-Object { python -m py_compile $_.FullName }
```

Tests use fake adapters and temporary SQLite databases. They do not need credentials and do not
create paid upstream tasks.

## Configuration

Copy `env.example` to the deployment environment and inject secrets outside Git. For dual-provider
traffic set `VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS=toonflow,paisio` and provide both API keys. Never
commit `.env`, API keys, authenticated response bodies, or copied production databases.

## Deployment and rollback

Before activation, record the current image digest and make recoverable backups of the deployed
source, catalog, environment, and SQLite database. Use SQLite's online backup API for a live database.
Build the reviewed commit as a dark image, run the local suite against a copied database, then switch
the service image and provider allowlist atomically.

Rollback restores the prior image, catalog, environment, and database backup. Do not rewrite active
jobs to another provider: queued jobs retain their persisted route plan, running jobs retain their
upstream task ID, and uncertain jobs require reconciliation.

The detailed acceptance and ten-round production verification procedure is in
`specs/008-video-multi-upstream/quickstart.md`.
