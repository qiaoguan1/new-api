# Plan

1. Audit production and staging configuration using boolean/equality checks only.
2. Build a root-only runtime environment from Docker metadata on the server: preserve staging
   identity/Callback values and import only provider/runtime capability variables from production.
3. Back up staging SQLite online, start an isolated candidate with Webhook delivery disabled, and
   verify health, readiness, integrity, migrations, and collector eligibility.
4. Drain and atomically replace the idle staging container, retaining the old container for rollback.
5. Re-sign and replay one existing delivered staging event; require HTTP 2xx.
6. Run ten no-cost verification rounds and document the downstream handoff.
