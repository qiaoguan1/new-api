# Plan

1. Read the target channel, ability, recent-log, and task state.
2. Create a mode-0600 rollback snapshot of the four channel rows and their
   abilities.
3. In one PostgreSQL transaction, set channels 27, 28, 40, and 41 to status 2
   and disable their abilities.
4. Restart only the NewAPI container to refresh its in-memory channel cache.
5. Verify target state, unrelated-channel invariants, public health, and the
   current upstream monitoring summary.
6. Record the deployment evidence and close Issue #123.

