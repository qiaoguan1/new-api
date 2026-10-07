# Issue186 production verification — 2026-10-07

## Verified

- Production verification returned: ordinary-user text video models7, image-reference profiles6, native runtime unchanged. Public `/api/pricing` returnedHTTP200, `video_catalog_status=ready`, and all three Grok image rows available with two profiles each.
- Both Python video gateways and the Go public frontdoor now use the issue186 candidates.
- Isolated canary `vjob_61a64afb2910ed701e19507741aa8c03` downloaded66191 bytes with audio/video, settledCNY0.675000 /337500 quota with matching user/token/task/log accounting and cross-user404. At the verified1.5 markup, upstream costCNY0.45; cumulative generation-test expenditureCNY4.95, below the authorizedCNY50 ceiling. No task was resubmitted.
- Six direct tests now all have bounded downloaded H264/AAC MP4 proof matching their authenticated SUCCESS bills.

- Local public-video and model Go suites pass.
- Latest candidate gateway passed all182 tests on the production host in an isolated test container.
- Six previously submitted image-reference cases have authenticated successful task bills: official single/multi-image CNY0.45 each; Grok Video3 and Imagine1.5 single/multi-image CNY0.90 each. Total approved test expenditure verified so far: CNY4.50.
- Live Grok Video3 scalar-output parsing and pinned-fetch caller-error propagation regressions are fixed.
- Production source baseline matches the feature base after line-ending normalization.
- The public site health endpoint returned HTTP200 with database-authoritative quota enabled during this run.

## Deliberate boundaries

- No real user/key permission, existing price, balance, historical replay/refund or schedule change was performed.
- Wan3, Wan3Prime, FLUX3, OmniFlash image modes and first/last/video/audio inputs still lack the required primary contract and exact live evidence in this release.
- PR188 remains draft; no CI status or merge is claimed.

## Release recovery

Foreground route refresh/rollback did not complete under unstable SSH. Original services were recovered first; the successful retry reused immutable tested candidates in an independent server process, retained current data, checked exact profile hashes, and bounded transient Docker-DNS validation.

The restored catalog comparison reported only a group-list order swap and placement of the same `pricing_version` hash on different rows. Actual prices were unchanged. Comparison now normalizes set-like lists and ignores only version-placement metadata; money and `pricing_revision` remain strict. Three operator regression cases pass, including rejection of real price/revision changes.

Canary-only configuration was corrected to use local/evidence-based prices without dynamic production reads. Final access validation uses an existing ordinary-role user key. No user permission was changed.

Private PostgreSQL/SQLite backups and stopped production rollback containers are retained. No database dump was restored over current transactions. New image modes remain bound to exact counts/specifications in the live capabilities/prices APIs.

Private server receipts remain under `/opt/ai-api-stack/backups/nody-multimodal-186-20261007`; credentials and signed result URLs are not included here.

Cleanup: four task-owned canary containers and the exact isolated database `xtai_issue186_canary` were removed after private PostgreSQL/SQLite audit backups. Production rollback containers and current production data are retained; audit backups can recover the removed test data.
