# Channel Monitor health policy

Issue #28 separates actionable incidents from stale or retired observations.

- `monitor_health_policy.py` is copied next to the production
  `generate-monitor-data.py` worker.
- `patch_monitor_generator.py` makes a fail-closed, idempotent source patch.
- `patch_monitor_frontend.py` labels inactive and stale states and shows
  monitoring coverage/configuration warnings in the internal operator UI.
- The policy never mutates channel state, pricing, credentials, or user quota.
- NewAPI `CHANNEL_TEST_FREQUENCY` remains unset: its all-channel routine can
  execute billable media requests and automatically disable channels.

Production deployment must first create a root-only rollback directory with
the original generator, generated data, and hashes. Run the patcher once,
compile both Python files, regenerate data, and independently validate the
active-only totals before keeping the change.
