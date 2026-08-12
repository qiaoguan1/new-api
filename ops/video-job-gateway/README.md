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

Copy `env.example` to the deployment environment and inject secrets outside Git. All reviewed video
providers may be registered, but registration alone does not make a provider eligible for a new
v2.1 task. Eligibility requires a reviewed catalog route, generation credentials, healthy state and
a ready exact task-level billing collector. A provider missing any one of these remains visible only
on the authenticated health endpoint and is excluded from new routing.

`VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS` is the final independent audit gate. Keep it at
`toonflow` while Paisio reports `cost_mismatch` and RollDek lacks terminal task evidence. Registering
or refreshing those channels does not silently approve them for traffic.

Paisio and RollDek use short-lived, read-only NewAPI account session files refreshed by
`ops/channel-monitor/scripts/refresh-video-provider-auth.py`. Toonflow's console token is mounted as
a root-owned 0600 file and reloaded for each settlement query, so an operator-approved replacement
does not require a gateway restart. Toonflow currently has no verified server refresh API and its
login is CAPTCHA-bound; the scheduled lifecycle check warns at 30/14/7/3/1 days and fails closed at
expiry instead of bypassing CAPTCHA. Never commit `.env`, API keys, session files, console tokens,
authenticated response bodies, or copied production databases.

Run the auth lifecycle command hourly under `flock`. Use the checked-in example config, keep the
real config/state and upstream credentials at mode 0600, and install a Toonflow replacement from a
private file rather than a command-line token:

```bash
python3 channel-monitor/scripts/refresh-video-provider-auth.py
python3 channel-monitor/scripts/refresh-video-provider-auth.py \
  --install-provider toonflow --replacement-token-file /root/private/toonflow.token.new
```

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
