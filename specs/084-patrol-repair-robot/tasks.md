# Tasks: Daily Patrol and Safe Self-Healing Robot

## Core engine (#85)

- [x] Write failing tests for policy validation and fail-closed unknown evidence.
- [x] Write failing tests for service, Docker, disk, backup, scheduled-job and video backlog checks.
- [x] Write failing tests for allowlist, cooldown, action budget and post-repair verification.
- [x] Write failing tests for incident open/reminder/recovery and notification sanitization.
- [x] Implement engine, policy, state/report persistence and NewAPI notification adapter.
- [x] Document checks, automatic actions and prohibited actions.

## Deployment (#86)

- [x] Write failing tests for loopback binding, bearer token file, API response and trigger coalescing.
- [x] Implement unprivileged control API and filesystem trigger.
- [x] Add hardened root oneshot/path/timer and unprivileged API units.
- [x] Add production install/backup/rollback procedure.
- [x] Deploy, exercise, inspect notification delivery and run ten verification rounds.
