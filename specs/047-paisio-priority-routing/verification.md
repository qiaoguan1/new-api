# Production Verification: Paisio-First Video Routing

**Verified**: 2026-08-10 Beijing time

**Reviewed merge**: `1ea134e6f1348e681b66bc87e98e1b8af010c6c9`

**Production release**: `/opt/xtai/releases/video-paisio-priority-1ea134e6`

**Recoverable backup**: `/opt/xtai/backups/video-paisio-priority-20260810-193100`

## Results

- The running container is healthy and uses the reviewed release and image.
- Ten runtime files match their reviewed Git blob hashes.
- Both Paisio and Toonflow are configured and eligible for new jobs.
- Five shared model/resolution combinations and 500 request identities all resolved to
  `paisio, toonflow`.
- Mini 720p remained Toonflow-only.
- Gateway tests: 19 passed.
- Channel-monitor regression tests: 162 passed.
- Ten production read-only verification rounds passed.
- Existing database rows: 145; active jobs during deployment: 0; duplicate request IDs: 0.
- No paid canary was created. No new real job arrived during the bounded validation window.
- Public health output exposed no provider name.
- Runtime logs contained no traceback, fatal, or panic marker after the switch.
- NewAPI pricing, channel monitoring, credentials, and CLR were not modified.

## Runtime Rule

Only a definite pre-creation Paisio failure without an upstream task ID may advance to Toonflow.
Uncertain outcomes and failures carrying a task ID remain on Paisio for reconciliation.
