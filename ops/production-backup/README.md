# Verified daily NewAPI backup

`newapi_daily_backup.py` creates a PostgreSQL custom-format dump and a root-only
archive of explicitly allowlisted recovery configuration. It verifies the dump
with the running PostgreSQL container, hashes all published files, atomically
renames the completed directory, and retains the newest 14 completed backups.

`install_newapi_backup.py` installs the worker at
`/opt/ai-api-stack/channel-monitor/scripts/newapi-daily-backup.py`, adds a
03:30 Asia/Shanghai root cron protected by `flock`, and installs a 14-day
logrotate policy. The installer creates a rollback directory before changing
the crontab or installed files and writes a checksum manifest for that rollback
bundle. Restore the saved `root.crontab.before` with `crontab`, and reinstall a
saved worker or logrotate file only when its corresponding `.before` file is
present.

For a non-destructive dump check:

```bash
docker compose exec -T postgres pg_restore -l \
  < backups/daily-newapi/newapi-YYYYMMDD-HHMMSS/database.pgdump \
  > /dev/null
```

The backup is local to the production disk. It protects against application and
database mistakes, but an encrypted off-host copy is still required for full
host or disk disaster recovery.
