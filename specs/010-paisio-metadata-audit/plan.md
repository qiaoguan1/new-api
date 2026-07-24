# Implementation Plan

1. Extend classic billing collection with a sanitized authenticated pricing snapshot.
2. Add pure model-intersection policy for metadata availability.
3. Add an idempotent production patch for the standalone daily scan script.
4. Verify against a production script copy and the live read-only Paisio endpoints.
5. Back up production, deploy, rerun yesterday's 08:20/08:30/08:40 workflow, and
   verify reconciliation and unchanged fail-closed behavior.
