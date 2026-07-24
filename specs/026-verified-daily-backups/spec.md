# Verified Daily NewAPI Backups

**Issue**: [#26](https://github.com/qiaoguan1/new-api/issues/26)

## Goal

Create a daily, root-only, self-verifying recovery bundle for the production
NewAPI database and its critical configuration.

## Requirements

1. Run daily at 03:30 Asia/Shanghai under a non-overlapping lock.
2. Publish a backup only after `pg_restore -l` accepts the custom-format dump.
3. Archive an explicit allowlist of active stack, Nginx, TLS, payment, and
   channel-monitor configuration without logging their contents.
4. Generate and verify SHA-256 checksums before an atomic directory rename.
5. Keep the newest 14 completed backups and delete only direct children of the
   dedicated backup root whose names match the completed-backup contract.

## Security properties

- Backup root and completed directories use mode 0700; files use mode 0600.
- Temporary failures are cleaned without touching unrelated paths.
- Secrets and database contents never appear on stdout or in metadata.
