# Code Review: relay-owned reference media v2.2

## Scope

The review covers the v2.2 public contract, official input-mode reservation,
Toonflow route mapping, task snapshots, Webhook payloads, reference media
fetch/probe security, container dependencies, and regression behavior for v2.1.

## Findings resolved

1. Major — A DNS allowlist check followed by an ordinary HTTP client lookup
   permitted a DNS rebinding race. The verifier now resolves once, rejects every
   non-global address, connects to a pinned address, and keeps TLS SNI and
   hostname verification on the original host.
2. Major — Large reference media verification could consume all request
   threads. A separate bounded verification semaphore now returns 429 before
   freezing or creating a task when full.
3. Major — Capability output initially advertised 15 total assets while the
   approved model contract permits 12. Capability output and every v2.2 route
   now enforce 12.
4. Major — The gateway container did not include the media probe dependency.
   The image now installs ffmpeg/ffprobe and still runs as the unprivileged
   gateway user.
5. Minor — Invalid Content-Length could escape as an unexpected error. It now
   maps to a deterministic pre-freeze size error.
6. Minor — Reference media host matching allowed arbitrary subdomains. The
   production contract now uses exact-host matching for the downstream private
   TOS origin.
7. Major — Companion images in an image-plus-audio request were not constrained
   to the approved downstream object-storage origin. They now require the same
   exact allowlisted public host and public DNS resolution before reservation.
8. Major — The durable Webhook outbox initially stored `result_url=null`, so a
   settled event did not itself provide the promised relay delivery URL. New
   events now persist the same authenticated relay content URL returned by task
   queries; tests also prove that upstream URLs and signed input URLs stay out.
9. Minor — The route enforced the 12-asset limit, but excess input could fall
   through to a generic route error. v2.2 now returns a deterministic pre-freeze
   asset-count validation error.

## Seven-criterion result

| Criterion | Result |
| --- | --- |
| Blindspots | Pass after fixes |
| Clarity/consistency | Pass |
| Maintainability | Pass; route capabilities and verifier are isolated |
| Security | Pass after pinned-DNS, TLS, byte-limit, hash and ffprobe fixes |
| Performance | Pass after bounded media verification concurrency |
| Documentation | Pass; spec, environment contract and downstream handoff added |
| Standards/style | Pass; full Python test suite and diff checks pass |

Security review: authentication remains constant-time, Webhook secrets never
enter payloads, media fetches are exact-host HTTPS with pinned public DNS, no
redirects, bounded bytes, content hash verification, ffprobe validation and an
unprivileged container. No command interpolation, SQL interpolation, credential
logging, arbitrary URL fetch, or public upstream identity exposure was found.

Unaddressed findings: 0.
