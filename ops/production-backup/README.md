# Verified daily NewAPI backup

`newapi_daily_backup.py` creates a PostgreSQL custom-format dump and a root-only
archive of explicitly allowlisted recovery configuration. It verifies the dump
with the running PostgreSQL container, hashes all published files, atomically
renames the completed directory, and retains the newest 14 completed backups.

## Extended recovery scope (#193)

The worker also reads a root-owned `0600` JSON configuration at
`channel-monitor/config/production-backup-roots.json`. An explicit
`--external-config /absolute/path.json` override is available. With no target
configuration the original PostgreSQL/core-only behavior remains available and
metadata explicitly says `core_only`, not full-server recovery. Missing optional
roots produce `partial`; a missing required root refuses publication entirely.

Production paths confirmed on 2026-10-10 can be configured as follows. These are
recovery sources only; installing this configuration does not start/stop channels,
change prices, or replay tasks. Re-verify mounts before installation if the host
layout has changed.

```json
{
  "schema": "xtai-production-backup-roots-v1",
  "roots": [
    {"name": "image-gateway", "path": "/opt/xtai-image-job-gateway/data", "required": true, "required_sqlite": ["image-jobs.sqlite3"]},
    {"name": "video-public", "path": "/opt/xtai/state/public-video-execution/data", "required": true, "required_sqlite": ["video-jobs.sqlite3"]},
    {"name": "video-v2", "path": "/opt/xtai/state/video-billing-v2-production/data", "required": true, "required_sqlite": ["video-jobs.sqlite3"]},
    {"name": "video-legacy", "path": "/opt/xtai/state/video-job-gateway/data", "required": true, "required_sqlite": ["video-jobs.sqlite3"]},
    {"name": "video-billing", "path": "/opt/xtai/secrets/video-billing", "required": true},
    {"name": "uploaded-media", "path": "/opt/ai-api-upload/data/media", "required": true},
    {"name": "toonflow-data", "path": "/opt/xtai-toonflow-image-adapter/data", "required": true},
    {"name": "nody-image-config", "path": "/opt/xtai-nodyhub-image-adapter/config.json", "required": true},
    {"name": "image25-config", "path": "/opt/xtai-image25-adapter/config.json", "required": true}
  ]
}
```

The core allowlist now includes daily notification delivery registration,
authenticated balance collection health, recharge and cost ledgers, manual weekly
review, and pricing-evidence/model policy configuration when present. The root
configuration itself is included in the protected archive. No directory is scanned
merely because it is under `/opt`, and neither filesystem roots nor a source
containing the destination backup directory are accepted.
The four database-bearing roots require their exact SQLite filenames, so an
existing but empty data directory cannot be misreported as a complete DB backup.

The worker captures current Docker recovery descriptors on **every** CLI run,
before and after the data/config snapshot, using only fixed container names and
`docker inspect` (no caller-provided shell). The private
`runtime/docker-containers.json` archive member records immutable image IDs,
container IDs, full Config/Env, mounts, aliases/networks, UID, restart and security
configuration. Credentials from descriptors never appear in stdout, metadata or
an extra published plain JSON file. A changed container ID/image/config/mount or
network during capture refuses the bundle.

The fixed allowlist has 14 required core dependencies:
`ai-api-stack-new-api-1`, `ai-api-stack-nginx-1`, `ai-api-stack-postgres-1`,
`ai-api-stack-redis-1`, `xtai-public-video-catalog176`,
`xtai-video-public-execution`, `xtai-video-job-gateway-v2-production`,
`xtai-video-job-gateway-video-job-gateway-1`,
`xtai-image-job-gateway-image-job-gateway-1`, `xtai-banana-chat-adapter`,
`xtai-nodyhub-image-adapter`, `xtai-image25-adapter`,
`xtai-toonflow-image-adapter`, and `media_upload-media-upload-1`.
Four currently running auxiliary dependencies are also captured by default:
`ai-api-stack-xt-egress-1`, `ai-api-stack-cli-proxy-api-1`,
`ai-api-stack-gpt-image-2-webui-1`, and `ai-api-stack-searxng-1`.
The staging video gateway is deliberately excluded. A protected policy may add a
`runtime_containers` array selecting all 14 core containers and any of those four
auxiliaries. This allows an operator to explicitly retire an auxiliary without
permanently breaking backup; unknown containers or missing core dependencies are
rejected, never silently skipped.

Descriptors alone do not include Docker image layers. Restoration requires the
recorded immutable images to be available locally, from a trusted registry or
from a separately verified source build. Until image availability and an off-host
copy are verified, this is not a full-host disaster-recovery claim.

## Snapshot and failure guarantees

- SQLite files are identified by their actual header. They are copied with the
  SQLite online backup API, including committed but uncheckpointed WAL data, and
  each resulting database must pass `PRAGMA quick_check`. Original databases are
  neither checkpointed nor replaced. Raw WAL/SHM/journal sidecars are not archived
  with a verified snapshot.
- Regular configuration, media, result and outbox files are copied through
  non-symlink paths with source device/inode/size/mtime checks. Source replacement,
  directory membership changes, special files, corruption, budget exhaustion or
  disk shortage refuse the incomplete bundle. A failed capture removes only its
  own correctly named temporary directory and never triggers retention.
- The default total capture budget is 900 seconds, maximum staged source data is
  32 GiB, at most 100,000 regular files, and destination free-space reserve is
  64 MiB. Preflight reserves enough
  room for staging plus an uncompressed-size archive estimate. Adjust the explicit
  CLI budgets only after validating source sizes and disk headroom. PostgreSQL
  commands and archive streaming also obey the time budget. CLI execution first
  issues `SELECT pg_database_size(current_database())` in an explicitly read-only
  PostgreSQL session with a 60-second timeout and checks twice that estimate
  against free disk and the byte budget before starting the dump. The original
  `pg_dump` direct-file path remains; its size is checked after completion, not
  intercepted per output block. A size preflight is a headroom estimate, not a
  promise against unlimited growth during the dump.
- Each completed directory is `0700`; every bundle file and tar member is `0600`
  and owned by root. Source UID/GID/mode are recorded privately for restoration;
  secrets themselves never enter worker stdout/stderr. Do not publish any bundle
  or metadata on a public HTTP directory or send it to users.
- SHA256SUMS protects the dump, archive, and metadata. Per-member content hashes,
  byte counts, snapshot methods and capture intervals are verified independently
  inside the archive before atomic publication. A tar traversal, link, duplicate,
  missing file, unexpected file or altered content is rejected.
- SQLite stores and PostgreSQL each have consistent snapshots **within the
  recorded capture window**, not one globally atomic cross-store point in time.
  If exact cross-store alignment is required, a separate operator-approved write
  maintenance window is necessary. This worker never quietly pauses production.

`coverage_status=configured_complete` means the explicitly configured recovery
roots were captured; it is not a claim that unrelated services, operating-system
packages, Redis cache, Docker image layers or an off-host copy were backed up.
Empty required media directories are recorded with their original owner/mode and
must be recreated during restoration even though no fake file is put in the tar.
Nested directory ownership/modes and empty subdirectories are also recorded and
checked for drift, so a restored results/outbox directory need not accidentally
become root-owned and unwritable by the non-root gateway.

## Non-destructive verification and restore rehearsal

From an import of `newapi_daily_backup.py`, call `verify_bundle(Path(bundle))` to
check all file and archive content proofs. Also run the PostgreSQL listing check
below against the installed PostgreSQL version. Do not treat a list check as a
successful data restoration.

Restore rehearsal is performed in a new restricted directory and an isolated
temporary PostgreSQL database, never over production:

1. Verify SHA256SUMS and all per-member content proofs before extracting anything.
   Preserve the original completed bundle and validate destination paths against
   the explicit recovery-root configuration; never trust archived absolute sources
   as automatic restore destinations.
2. Restore the custom dump into the isolated database and verify the expected
   users, options, billing/task tables and counts. No user balance repair or task
   replay is part of a rehearsal.
3. Read each restored SQLite with `mode=ro`, run `PRAGMA quick_check`, and verify
   task identifiers, statuses, bill/outbox records and referenced result files.
   The recorded per-store intervals determine what was in the bundle.
4. Recreate configured empty directories. Apply the privately recorded original
   UID/GID/mode only to verified explicit targets (gateway files may be UID 10002).
   Keep billing credentials `0600` and never broaden host permissions.
5. Audit unknown/running tasks against their original upstream identifiers before
   starting workers. Preserve drain/owner/state controls until an operator has
   decided how to resume; do not create replacement generation requests.

Installing or testing the extended worker should first back up the **currently
installed** worker and root configuration and compare their hashes with the
reviewed baseline. The source in this repository does not override unreviewed
production-only protections. For a first manual verification without deleting any
previous daily backups, use a new isolated `--backup-root` with `--retain 14`, or a
retention value larger than the current number of completed bundles. Normal daily
retention remains unchanged.

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
