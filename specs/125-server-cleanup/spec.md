# Safe production server cleanup

## Objective

Audit and safely clean the production relay server so that storage and runtime
state are simpler and more efficient without deleting business data, weakening
security controls, or interrupting service.

## Acceptance criteria

- Every cleanup target has an exact identity, measured size, dependency check,
  and recovery explanation.
- Databases, credentials, active mounts, active images, business evidence, and
  valid backups are preserved.
- Broad prune operations and ambiguous recursive deletion are not used.
- Before-and-after disk, inode, memory, container, service, endpoint, scheduled
  task, and backup states are recorded.
- Core public endpoints remain healthy and containers do not gain unexpected
  restarts.
- Ambiguous or operationally valuable candidates are reported for an explicit
  user decision instead of being deleted.

## Non-goals

- Do not alter routing, pricing, users, quotas, channels, models, or billing.
- Do not rotate credentials or change firewall, SSH, application, or database
  policy as part of a storage cleanup.
- Do not delete rollback assets unless their replacement and recovery path are
  proven.

