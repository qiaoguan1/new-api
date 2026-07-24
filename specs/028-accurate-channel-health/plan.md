# Implementation Plan

1. Add failing unit tests for active-only aggregation and health states.
2. Implement a dependency-free monitor health policy module.
3. Add a deterministic patcher for the production generator.
4. Back up and patch the production generator, then regenerate monitor data.
5. Verify cron/timezone invariants, data reconciliation, and ten health rounds.
6. Complete review, push the branch, open a PR, and record merge gates.
