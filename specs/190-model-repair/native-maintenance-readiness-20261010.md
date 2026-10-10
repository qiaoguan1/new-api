# Native maintenance readiness — 2026-10-10

This is a partial preparation record, not deployment evidence or a completed
dependency attestation. No native promotion was launched by the takeover
operator, and no maintenance approval was refreshed.

## Authorization and unchanged production

- Human decision: A, observed at 2026-10-10 12:15:34 UTC. The release guard's
  one-hour freshness window expires
  2026-10-10 13:15:34 UTC (21:15:34 Asia/Shanghai).
- Scope: pause new text/image/video submissions, preserve existing task reads
  and settlement, promote only the frozen native image-policy candidate, then
  verify and reopen. No paid generation, task replay, recharge, refund,
  user-key/channel/price change, financial-store restoration or video-container
  replacement is permitted.
- Read-only server check at approximately 13:10 UTC found no
  `native-rollout.json`, no image DRAIN marker, and original nginx SHA256
  `731ccc03f5f60eb31d69251d73aba53e99ee1e96e01358c693f1722dae2a5050`.
- Previously deployed adapter work remains outside this takeover's changes.

## Empty VERSION correction

The original production `VERSION` and the frozen candidate-source `VERSION`
were separately read on the authorized server. Both are zero-byte regular
files with SHA256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

The maintenance validator now allows an empty version only with the exact
`explicit_empty_version_attestation` object containing
`serving_status_version: ""` and the frozen zero-byte SHA256. Initial and
post-start HTTP verification compare the actual `/api/status` version with
the manifest and re-read the protected original `VERSION` file. Missing or
mismatched evidence fails closed. The candidate image was not rebuilt.

Local verification:

- `python -m unittest discover -s specs/190-model-repair -p test_promote_native.py -v`:
  37 PASS.
- Independent read-only review: all seven criteria passed; zero findings.
- Independent `python -B -m unittest discover -s specs/190-model-repair -p test_promote_native.py -v`:
  37 PASS.
- `git diff --check`: PASS.

The existing stopped-state guard changes were inherited from the parent and
were not modified by this correction.

## Separate approval-expiry hardening

The parent subsequently authorized three additional fresh-approval checks:
immediately before DRAIN creation, before gated nginx installation, and before
SIGTERM. Each re-reads the protected evidence and checks current time; none
changes `approved_at` or `expires_at`. Expiry before gate installation or TERM
uses owned-admission recovery while the original remains running. Recovery
failure preserves maintenance rather than stopping the original or overwriting
foreign state.

Three new mock regressions were run RED first (all three failed as expected),
then GREEN with the three checks implemented: 40 PASS. Independent read-only
seven-criterion review found zero findings and independently reran 40 PASS.
The RED fixture's printed `native_deployed` strings describe simulated Docker
state only; no production deployment was executed.

## Public proxy provenance still requires resolution

Two retained operator release artifacts identify the exact live public binary
SHA256 `02bcbc3af485c82f97746bd8e656b72e8b9e2e40947ebb30be68c5d9f0e49484`:

| Retained release suffix | artifact VCS reference |
| --- | --- |
| `4309756fc2854fe8a3a72f89569058a3` | `be1e8053d7d69665ea1f7d11b82000f406041d44` |
| `2f18e153411d44bf9ea976a0bcf5b622` | `9c0b0b0ed7eace29295c0f2e9b7f16c469f6c310` |

Both are under `/opt/ai-api-stack/releases/nody-operator-testing-<suffix>`.
Their `artifact.json` files contain binary, gzip, gateway-source and catalog
hashes, an operation identity and a VCS reference. Their public directories
retain the binary and Dockerfile, but no public Go source tree.

`prepare_operator_release.py` explicitly supplies the binary hash as the
public image's source label. That label is therefore binary integrity evidence,
not by itself proof that the captured older three Go files produced the live
binary. A source-to-binary provenance binding must be established before a
dependency audit can truthfully be marked complete.

The actual serving binary was inspected with a trusted, network-none, read-only
Go build-info reader. It reports Go1.26.5, trimpath, CGO0 and amd64, but no
embedded VCS revision. This confirms binary metadata, not the missing compile-input
binding. No public binary was executed by this metadata inspection.

Independent read-only Git comparison subsequently established that the two
artifact VCS references have identical public source, including five runtime Go
files with `media_input` and `operator_testing`. All three downloaded older
public Go files have different Git blob identities from those references. The
retained older source tree therefore cannot be treated as the binary's exact
source, and its dependency review must remain a qualified historical-source
review rather than complete live-binary certification.

## Remaining admission prerequisites

Two protected root-owned0600 audit inputs were saved without applying them:

- `native-nginx.gated.conf` SHA256
  `a30598899ab811ffc6fdd26e0b7b1060d559e5256046853e3946b68eb2c4e8f5`.
- `native-maintenance-approval.json` SHA256
  `4253e9ea12d1ecd8245731d9c1360a94b2c33b4700ca7e845f9c86ad9f16815b`.
  It records the original approved/expiry times and is now expired.

The post-write server check at 13:15:55 UTC confirmed the serving nginx bytes
were unchanged and `native-rollout.json` was absent. The approval expired at
13:15:34 UTC; no native rollout was launched before or after that deadline.

A final read-only server check at approximately 13:17:17 UTC confirmed original
native identity
`b69f77995b3a7779885f74b9f99464b0f9f926dffa0ddb542f259bf09b7f1166`
and image
`sha256:7d231e67f87f98ace75943882f3b8128dadfd7963ff579b31d03a6999c30d2f2`
were still serving (running, not restarting). The three already-promoted image
adapters and all four video containers were also running and not restarting.
The nginx hash remained original, the candidate manifest and native journal
were absent, and the approval timestamps remained unchanged. These are process
state checks, not new paid generation or a full functional acceptance test.

Three remaining inputs are not complete: `native-candidate.json`,
`native-dependency-evidence.json`, and `native-route-audit.json`. A fresh
protected backup after the latest adapter promotion must also be verified
before any native switch. No audit is signed complete merely because its
validator accepts the schema or mock tests pass.

The one-hour approval must not be extended, re-dated or inferred anew. If these
prerequisites cannot be completed safely within the existing window, keep the
serving native unchanged and request a new human decision for a later window.
