# Tasks: Daily Patrol and Safe Self-Healing Robot

## Core engine (#85)

- [ ] Write failing tests for policy validation and fail-closed unknown evidence.
- [ ] Write failing tests for service, Docker, disk, backup, scheduled-job and video backlog checks.
- [ ] Write failing tests for allowlist, cooldown, action budget and post-repair verification.
- [ ] Write failing tests for incident open/reminder/recovery and notification sanitization.
- [ ] Implement engine, policy, state/report persistence and NewAPI notification adapter.
- [ ] Document checks, automatic actions and prohibited actions.

## Deployment (#86)

- [ ] Write failing tests for loopback binding, bearer token file, API response and trigger coalescing.
- [ ] Implement unprivileged control API and filesystem trigger.
- [ ] Add hardened root oneshot/path/timer and unprivileged API units.
- [ ] Add production install/backup/rollback procedure.
- [ ] Deploy, exercise, inspect notification delivery and run ten verification rounds.

