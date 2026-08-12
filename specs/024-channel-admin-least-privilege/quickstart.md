# Deployment and rollback

## Deployment invariants

- Generate the token on the server with at least 32 random bytes.
- Store it only in root-readable `/etc/channel-monitor-admin.env` and
  `nginx/auth/channel-monitor-token.inc`; never print it.
- Mount the Nginx include read-only through Compose.
- Keep scripts root-owned and non-writable by `channel-monitor-admin`.
- Give the service user ownership only of `upstreams.json`,
  `report-baseline.json`, `upstream-credentials.json`, and `data/`.
- Enable the path-triggered generator helper. Only that short-lived, root-owned
  unit definition may add `SupplementaryGroups=docker`; the network-facing admin
  unit must not receive the Docker group or socket.
- Keep configuration, internal ledger and credential files mode 0600. The unit
  uses umask 0022 only so privacy-sanitized monitor JSON remains readable by the
  read-only Nginx container.

## Verification

1. Run all three patchers twice on copies and confirm the second run is a no-op.
2. Validate `docker compose config`, the hardened systemd unit, and Nginx syntax.
3. Confirm a direct bridge request without the header returns 403.
4. Confirm the existing Basic-Auth route still returns 401 without credentials
   and 200 with administrator credentials.
5. Save an unchanged configuration payload, run balance collection, and regenerate
   monitor data; verify no credential value appears in responses or logs.
6. Confirm the service user has no supplementary `docker` group.

## Rollback

Restore the timestamped admin source, Nginx config, Compose file, and systemd unit;
remove only the issue-specific token include/mount; run `docker compose config` and
`nginx -t`; then restart the old service and recreate only the Nginx container.
