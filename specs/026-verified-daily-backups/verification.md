# Verification

**Pull request**: [#27](https://github.com/qiaoguan1/new-api/pull/27)

## Production deployment

- Installed worker:
  `/opt/ai-api-stack/channel-monitor/scripts/newapi-daily-backup.py`
- Cron: daily at 03:30 under `CRON_TZ=Asia/Shanghai`, protected by
  `/run/lock/newapi-daily-backup.lock`.
- Logrotate: `/etc/logrotate.d/newapi-daily-backup`, 14 rotations, compression,
  and mode-0600 creation.
- Installer rollback bundle:
  `/opt/ai-api-stack/backups/issue26-daily-backup-20260724-173415`
- First completed backup:
  `/opt/ai-api-stack/backups/daily-newapi/newapi-20260724-173422`

## Evidence

- Local and server unit suites: 9/9 passed.
- Python bytecode compilation and diff checks passed.
- Installed worker SHA-256 exactly matches the reviewed source.
- Backup root and completed directory are mode 0700; all four published files
  are mode 0600 and owned by root.
- `SHA256SUMS` verifies the database dump, metadata, and recovery archive.
- `pg_restore -l` independently accepts the published custom-format dump.
- The recovery archive contains 19 allowlisted regular files and no absolute,
  parent-traversal, symbolic-link, or hard-link members.
- Logrotate dry-run passes.
- NewAPI returns HTTP 200 and no container is unhealthy.
- Ten final rounds passed manifest, `pg_restore`, permissions, cron uniqueness,
  NewAPI health, and container health checks.
- GitHub reported no status checks for Pull Request #27; the repository has no
  recorded Actions runs. The clean merge state and the local/server/production
  evidence above are the available verification gates.

## Residual boundary

This is a same-host backup. It covers database/application mistakes but not loss
of the entire server or disk; encrypted off-host replication remains a separate
disaster-recovery enhancement.
