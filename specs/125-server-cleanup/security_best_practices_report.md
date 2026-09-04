# Production security best-practices report

## Executive summary

The relay is currently available and its main perimeter controls are active.
No critical issue or current public diagnostic exposure was found. The review
identified two high-priority resource-exhaustion weaknesses, four medium/low
hardening issues, and two log-retention risks. These should be fixed as
separate, tested changes because request-size, streaming, custom footer, and
container recreation behavior can affect existing clients.

Confirmed controls:

- UFW is active with default-deny incoming traffic; public listeners are 22,
  80, and 443.
- Fail2ban is active for SSH. Password and keyboard-interactive SSH login are
  disabled; root is key-only.
- Production credential and secret files checked by the audit are mode 0600.
- Production pprof is disabled and port 8005 is not listening.
- TLS and SMTP certificate verification are not disabled.
- Docker has no stopped containers, unused images, unused volumes, or BuildKit
  cache after the approved cleanup.
- The latest daily database backup checksum is valid.

## High priority

### SEC-001 — GO-HTTP-002 — High — public request limit is effectively 10 GiB

- Location: `common/init.go:182`, `common/gin.go:60-64`; production
  `/opt/ai-api-stack/nginx/conf.d/default.conf:24-33`.
- Evidence: code defaults the decoded request-body limit to 128 MB, but the
  running NewAPI container sets `MAX_REQUEST_BODY_MB=10240`; the main Nginx
  server uses `client_max_body_size 0`. The video submission location has a
  separate 256 KiB limit, but the general API does not.
- Impact: an authenticated or otherwise accepted oversized request can consume
  substantial disk, memory, CPU, bandwidth, and worker time; compressed input
  increases decompression-bomb risk.
- Fix: inventory the few endpoints that genuinely require large uploads, set a
  conservative global limit, and add explicit larger per-route limits only for
  those endpoints. Verify streaming and media uploads before rollout.
- Mitigation: retain the existing 256 KiB video-contract limit and add rate and
  concurrency limits at Nginx for expensive routes.
- False-positive notes: body storage may spool rather than hold all bytes in
  memory and some routes have lower limits, but 10 GiB remains the effective
  application-wide ceiling.

### SEC-002 — GO-HTTP-001 — High — Go HTTP servers omit explicit timeouts

- Location: `main.go:200-203` for the primary server and `main.go:155` for the
  optional diagnostic server.
- Evidence: the main `http.Server` sets only `Addr` and `Handler`; the optional
  pprof path uses `http.ListenAndServe`. Neither defines
  `ReadHeaderTimeout`, `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, or
  `MaxHeaderBytes`.
- Impact: slow or malformed clients that reach the application listener can
  retain connections and consume file descriptors and goroutines.
- Fix: use explicit server objects with calibrated header/idle timeouts and a
  justified header limit. Preserve SSE/streaming behavior by testing route
  classes before enforcing write timeouts.
- Mitigation: Nginx currently fronts the public application and reduces direct
  exposure.
- False-positive notes: Nginx lowers practical risk, but does not make the Go
  server's unlimited defaults safe for internal or proxy-bypass traffic.

## Medium priority

### SEC-003 — REACT-XSS-001 — Medium — custom footer HTML is not sanitized

- Location: `web/src/components/layout/components/footer.tsx:225-237`.
- Evidence: `footerHtml` is passed directly to `dangerouslySetInnerHTML`; the
  DOMPurify helper used by the generic HTML and Markdown components is not used
  in this path.
- Impact: a stolen administrator session, compromised configuration endpoint,
  or unsafe pasted markup can become persistent script execution for visitors.
- Fix: sanitize through a centralized allowlist or replace arbitrary HTML with
  structured footer fields. Test existing custom markup before deployment.
- Mitigation: the value is administrator-controlled and secret files are
  restricted.
- False-positive notes: administrator-only input lowers likelihood but does not
  remove stored-XSS impact.

### SEC-004 — REACT-HEADERS-001 / GO-HTTP-004 — Medium — main site lacks baseline headers

- Location: production `https://api.aixingtuyun.com/` and
  `/opt/ai-api-stack/nginx/conf.d/default.conf:24-66`.
- Evidence: the runtime response contains none of CSP, frame protection,
  nosniff, referrer policy, or permissions policy at the root. A stricter header
  block exists elsewhere in the Nginx configuration, not on the main root
  response.
- Impact: weaker defense in depth against XSS, clickjacking, MIME confusion,
  and referrer/device-feature leakage.
- Fix: apply a compatible baseline centrally at Nginx; introduce CSP in
  report-only mode first and verify analytics, payments, and embedded content.
- Mitigation: HTTPS is already in use and React escapes ordinary JSX strings.
- False-positive notes: the production origin was tested directly; no evidence
  showed a separate edge adding the missing headers.

### SEC-005 — GO-CONFIG-001 — Medium — historical releases duplicate live credentials

- Location: `/opt/xtai/releases`, `/opt/xtai/backups`, and
  `/opt/ai-api-stack/backups`.
- Evidence: the audit found 22, 13, and 8 historical `.env` or upstream
  credential files respectively. All checked files are mode 0600, but they
  expand the number of plaintext copies that must be protected and rotated.
- Impact: filesystem backup leakage or root-level compromise exposes more
  credential history and may reveal keys that remain valid.
- Fix: approve a retention policy, keep only current plus one verified previous
  release, move durable secrets to canonical secret files, and exclude them
  from code snapshots. Rotate any key whose historical copies are deleted only
  if its validity cannot be proven.
- Mitigation: ownership and permissions are restricted to root or the dedicated
  service account.
- False-positive notes: some historical values may already be expired; contents
  were deliberately not printed or copied during this audit.

## Low priority

### SEC-006 — GO-DEPLOY-002 — Low (dormant) — pprof binds all interfaces when enabled

- Location: `main.go:38` and `main.go:153-158`.
- Evidence: importing `net/http/pprof` registers the default mux and the enabled
  path listens on `0.0.0.0:8005` without authentication.
- Impact: a future configuration error could expose stack, heap, command-line,
  and performance data.
- Fix: bind loopback or a dedicated internal address, use an explicit mux and
  timeouts, and require network/application authorization.
- Mitigation: production does not enable pprof and port 8005 is closed.
- False-positive notes: this is a risky default, not an active exposure.

## Operational reliability findings

### OPS-001 — Medium — Docker JSON logs have no size/rotation limit

- Location: all 12 running Docker containers.
- Evidence: every container uses `json-file` with an empty option map; the
  largest current file is about 78 MB.
- Impact: recurring errors can fill the disk and cause a service or database
  outage.
- Fix: add `max-size` and `max-file` to version-controlled deployment manifests
  and roll containers one at a time with health checks and rollback manifests.
- Mitigation: disk use is currently 12% after cleanup and monitoring is active.

### OPS-002 — Low — journald has no explicit storage cap or retention period

- Location: `/etc/systemd/journald.conf` and its drop-in directory.
- Evidence: no active `SystemMaxUse`, `SystemKeepFree`, or `MaxRetentionSec`
  setting was found; journals currently use about 565 MB.
- Impact: sustained noisy services or attacks can cause unbounded log growth.
- Fix: choose a retention window that preserves security investigations, then
  configure a disk cap and alert before the cap is reached.
- Mitigation: current journal size is modest and UFW/Fail2ban are active.

## Coverage note

The full Go test suite passed earlier in Issue #125. `govulncheck` is not
installed in the local verification environment, so this audit does not claim
that all dependency CVEs were scanned. Installing and running it should be a
separate reproducible CI change rather than an ad-hoc production download.
