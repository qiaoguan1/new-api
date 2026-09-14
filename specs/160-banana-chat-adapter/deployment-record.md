# Issue 160 Deployment Record

## Deployed components

- Adapter image: `xtai/banana-chat-adapter:issue160-5650a34e`
- Adapter directory: `/opt/xtai-banana-adapter`
- Nginx route: `/internal-upstreams/banana/`
- NewAPI channel: #53 `Banana Flash · 稳定适配`
- Stable model published: `banana-flash`

The adapter is an independent non-root, read-only container on `app-net`. It does not reuse the
existing image-job-gateway credentials or modify that gateway.

## Provider behavior

| Path | Provider | Result |
| --- | --- | --- |
| Direct Flash | Rolldek | HTTP 200, 26.18s |
| Direct Pro | Rolldek | HTTP 200, 22.54s |
| Direct Flash | Haina vip | HTTP 200, 26.01s |
| Direct Pro | Haina vip | Provider pool unavailable; not billed |
| Adapter dark-run Flash | Haina vip | HTTP 200, 10.89s |
| Adapter dark-run Pro | Rolldek | HTTP 200, 39.79s |
| Production-path Flash | Haina vip | HTTP 200, 11.54s |
| Production-path Pro | Rolldek | Failed after 132s; not billed |

The production channel was reduced to Flash only after the Pro long-tail failure. The adapter keeps
the Pro implementation for later validation, but NewAPI does not advertise or route it.

## Recovery

- Stack/Nginx bundle: `/opt/ai-api-stack/backups/issue160-banana-adapter-20260914-231809`
- NewAPI database recovery: `/opt/ai-api-stack/backups/issue160-newapi-recovery/newapi-20260914-231810`

Rollback disables/deletes channel #53 and its abilities, restores the backed-up Nginx file while
recreating only Nginx, and stops `/opt/xtai-banana-adapter`. Existing images, databases, and media
gateways remain untouched.

## Final verification

- Daily audit: 10 enabled, 10 healthy, 0 failed, 0 critical alerts.
- Channel #53 catalog: HTTP 200, `banana-flash` visible with no missing model.
- Public API and adapter readiness: HTTP 200.
- Candidate container, temporary provider copies, and temporary NewAPI tokens were removed.
