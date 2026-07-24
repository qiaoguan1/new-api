# Implementation Plan

1. Add tests for newest-per-source selection, expiry, completeness, and kinds.
2. Implement a dependency-free rolling actual-cost selector.
3. Add a fail-closed, idempotent patcher for the deployed pricing worker.
4. Validate a patched copy against production data in an isolated directory.
5. Back up production, deploy, run dry-run, and manually review every newly
   covered model before any transaction.
6. Apply only if all safety gates pass, then verify options and ten rounds.
7. Complete review, PR, merge, and project updates.
