# Production cleanup record

## Scope and safeguards

- Production host: `api.aixingtuyun.com`.
- No routing, pricing, channel, user, quota, billing, database, credential,
  firewall, or service configuration was changed.
- No paid generation request was sent and no running container was restarted.
- Broad Docker prune operations were not used. In particular,
  `docker system prune` and `docker volume prune` were prohibited.

## Baseline

- Root filesystem: 99 GB total, 64 GB used, 31 GB available (68%); inode use
  25%.
- 12 running containers; every container had `RestartCount=0`.
- No failed systemd units. UFW and Fail2ban were active.
- Public listeners were limited to 22, 80, and 443. The admin port 8791 was
  bound to the Docker bridge and restricted by UFW.
- The latest daily PostgreSQL backup had valid SHA-256 checksums and the daily
  backup set retained 14 copies as configured.

## Exact cleanup performed

| Target | Dependency proof | Result |
|---|---|---|
| APT package archives | Download cache only | 109,350,136 bytes reduced to 24,576 bytes |
| `/root/.cache/go-build` | `go env GOCACHE` matched exactly; rebuildable compiler cache | 529,949,657 bytes reduced to 4,324 bytes |
| Unused BuildKit records older than 168 hours | 0 active build records; age-bounded prune | Docker reported 427 MB reclaimed |
| Three empty anonymous Docker volumes | Exact IDs; zero files, zero consumers, 4,096 bytes each | Removed individually |

The exact empty volume IDs removed were:

- `6f04f4d5532cc32064ab97688f686120ee70db1d0008917e1d5b93197e7d2e4d`
- `b3b98539ec11286ee747e28a17bedcdba27215534b52665bc95233d598dbd1ea`
- `f581e932c9048098705b122f0edd7de7637b42096625c9f177cbb1812d8accdc`

Measured filesystem use fell by 1,160,847,360 bytes (about 1.08 GiB).

## User-approved second cleanup

After the first-pass report, the user explicitly approved deletion of the five
previously retained categories. Every target was revalidated immediately before
deletion.

| Approved target | Final validation | Result |
|---|---|---|
| Five named Go/Bun build-cache volumes | Zero container consumers; exact Docker mountpoints; no PostgreSQL markers | Removed; 4,025,842,552 bytes |
| Dirty checkout `web/default/node_modules` | Exact resolved path, regular directory, lockfile present, zero process/container references | Removed; 1,590,337,381 bytes |
| Historical Docker state | Exactly 32 non-running containers; image set excluded every running image ID | Removed 32 containers and 71 non-running image IDs individually |
| Three historical PostgreSQL volumes | Exact IDs, zero consumers, PostgreSQL markers present | Removed after explicit approval |
| Previous offline Windows client set | Two exact `.previous` directories, zero process/container references | Removed; 1,646,525,817 bytes |

The eight approved Docker volumes (five build caches and three historical
PostgreSQL data volumes) contained 4,306,241,053 bytes in total. The second pass
reduced filesystem use by 13,129,687,040 bytes (about 12.23 GiB). Across both
passes, measured filesystem use fell by 14,290,534,400 bytes (about 13.31 GiB).

## User-approved BuildKit cleanup

The user separately approved removal of the remaining BuildKit cache after its
purpose and rebuild-time tradeoff were explained. Immediately before deletion,
Docker reported 449 cache records, zero active records, 41.34 GB reclaimable,
and no running `docker build` or `docker buildx` process.

`docker builder prune -af` removed the BuildKit cache without touching images,
containers, volumes, source, backups, or business data. Docker reported
41.34 GB removed and the filesystem measurement showed 40,816,181,248 bytes
freed (about 38.01 GiB). Across all cleanup passes, measured filesystem use fell
by 55,106,715,648 bytes (about 51.32 GiB).

## Final rebuildable-cache cleanup

The Go module download cache was resolved with
`/usr/local/go/bin/go env GOMODCACHE` to the exact regular directory
`/root/go/pkg/mod`. It had no process references and no Go compiler process was
active. `go clean -modcache` removed 1,100,013,524 bytes of cache content; the
filesystem measurement showed 1,185,849,344 additional bytes freed. The next Go
build must download modules again.

Across every pass, measured filesystem use fell by 56,292,564,992 bytes (about
52.42 GiB).

## Post-cleanup verification

- Root filesystem after the final cache pass: 12 GB used, 83 GB available (12%).
- All 12 running containers remained up with `RestartCount=0`.
- Ten consecutive verification rounds after each of the three cleanup passes
  (120 checks total) returned HTTP
  200 for the main site,
  API status, video gateway health, and video gateway readiness endpoints.
- No failed systemd units.
- `channel-monitor-admin.service`, `channel-monitor-patrol-repair.timer`, and
  `xtai-video-relay-gateway-audit.timer` were active.
- Daily pricing and upstream balance artifacts were updated at 08:40 and 08:20
  Beijing time respectively on 2026-08-16.
- Production `ENABLE_PPROF`, `TLS_INSECURE_SKIP_VERIFY`, and
  `SMTP_INSECURE_SKIP_VERIFY` all evaluated false; port 8005 was not listening.
- Full Go test suite passed after correcting two pre-existing test defects.
- Docker now contains 12 containers (all running), 11 images (all referenced by
  running containers), and two volumes (the active PostgreSQL and Redis data
  volumes). BuildKit has zero cache records. There are no unused images,
  stopped containers, or unused volumes.

## Retained after the approved cleanup

| Candidate | Approximate size | Reason retained |
|---|---:|---|
| Historical releases and maintenance backups | about 1.0 GB | Recovery evidence without an approved retention policy |
| systemd journal and application logs | about 670 MB | Operational and security evidence; no approved retention period |

## Follow-up decisions

1. Choose a filesystem release/backup retention policy before removing old
   release directories or maintenance backups.
2. Approve a separate rolling deployment before enabling Docker JSON log
   rotation. All running containers currently use `json-file` without
   `max-size` or `max-file` limits.
3. Approve a separate HTTP hardening change for response security headers,
   server timeouts, and safer diagnostic-listener defaults.

The prioritized evidence and remediation details are in
`security_best_practices_report.md`.

## Security hardening findings (not changed in this cleanup)

### GO-HTTP-001 — Medium — application HTTP server has no explicit timeouts

- Location: `main.go`, production server construction near the
  `http.Server` call.
- Evidence: only `Addr` and `Handler` are set; `ReadHeaderTimeout`,
  `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, and `MaxHeaderBytes` are absent.
- Impact: a direct or proxy-bypassing slow-client path could retain connections
  and consume resources longer than intended.
- Recommended fix: define bounded timeouts and header size appropriate for API
  streaming, then verify SSE and long-running responses before deployment.
- Current mitigation: the application is behind Nginx and no outage or active
  exploit evidence was observed.
- False-positive note: Nginx reduces public exposure but does not make the
  application listener's defaults intrinsically safe.

### GO-DEPLOY-002 — Low (dormant) — optional pprof listener binds all interfaces

- Location: `main.go`, `http.ListenAndServe("0.0.0.0:8005", nil)`.
- Evidence: enabling `ENABLE_PPROF=true` would expose the default pprof mux on
  all interfaces without an application authentication layer.
- Impact: an accidental configuration change could expose diagnostic and
  performance data.
- Recommended fix: bind loopback or an internal-only address, use a dedicated
  server with timeouts, and require network or application authentication.
- Current mitigation: production `ENABLE_PPROF=false` and port 8005 is not
  listening.
- False-positive note: this is not a current public exposure; it is a risky
  fail-open deployment default.

### REACT-HEADERS-001 / GO-HTTP-004 — Medium — browser security headers absent

- Location: the public `https://api.aixingtuyun.com/` response and Nginx/app
  response configuration.
- Evidence: runtime HEAD response contained none of HSTS, CSP,
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, or
  `Permissions-Policy`.
- Impact: weaker defense in depth against content injection, clickjacking,
  MIME confusion, and transport downgrade scenarios.
- Recommended fix: add a tested baseline at Nginx, beginning with report-only
  CSP if compatibility is uncertain.
- Current mitigation: HTTPS is in use and no active exploit evidence was
  observed.
- False-positive note: the test covered the production origin response; no
  evidence showed another edge layer adding these headers.

### REACT-DOM-001 — Medium — configured footer HTML bypasses sanitization

- Location: `web/src/components/layout/components/footer.tsx`, the
  `dangerouslySetInnerHTML` rendering of `footerHtml`.
- Evidence: the footer path does not use the DOMPurify helper used by the
  generic HTML and Markdown components.
- Impact: a compromised admin/configuration write path could become stored XSS
  affecting site visitors.
- Recommended fix: sanitize the footer with a documented allowlist or replace
  raw HTML with structured footer fields; test required custom markup before
  rollout.
- Current mitigation: the value is administrator-controlled rather than direct
  anonymous-user input.
- False-positive note: administrator trust lowers likelihood but does not
  eliminate the impact of stolen admin credentials or unsafe pasted HTML.
