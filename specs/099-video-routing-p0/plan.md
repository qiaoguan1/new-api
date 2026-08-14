# Implementation Plan

1. Reproduce the production regression from the task ledger and image catalog.
2. Use the clean committed gateway source that already contains issue #90 and
   the audited SD3/SD4 catalog.
3. Add a release-integrity module and Docker build gate requiring a full commit
   SHA plus the expected catalog SHA-256.
4. Bump the catalog revision so downstream caches cannot confuse the repaired
   catalog with the stale production revision.
5. Run focused and full tests, then comprehensive and security review.
6. Build on the server from an archive of the exact commit, deploy staging,
   preserve production state, deploy production, and run ten no-charge rounds.
