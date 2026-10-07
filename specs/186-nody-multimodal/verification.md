# Issue186 verification checkpoint — 2026-10-07

## Verified

- Local public-video and model Go suites pass.
- Latest candidate gateway passed all182 tests on the production host in an isolated test container.
- Six previously submitted image-reference cases have authenticated successful task bills: official single/multi-image CNY0.45 each; Grok Video3 and Imagine1.5 single/multi-image CNY0.90 each. Total approved test expenditure verified so far: CNY4.50.
- Live Grok Video3 scalar-output parsing and pinned-fetch caller-error propagation regressions are fixed.
- Production source baseline matches the feature base after line-ending normalization.
- The public site health endpoint returned HTTP200 with database-authoritative quota enabled during this run.

## Not completed or not claimed

- The six-case complete delivery proof and isolated public-wallet canary are not accepted yet. The free-only server run stopped at the delivery-proof gate; it submitted no paid task and did not prepare/promote production changes.
- The operator verification helper's cleanup-confirmation check failed after media probing. It now also confirms sandbox absence through an exact-name Docker container listing rather than depending on CLI error wording; that correction still needs live verification. The original CLI error text was not recovered, so its cause is not asserted.
- SSH repeatedly timed out during connection/banner exchange or reset after connecting. These errors do not establish that the public service is down.
- No production gateway/frontdoor switch, user/key/price/balance modification, historical replay/refund, or schedule change was performed.
- Wan3, Wan3Prime, FLUX3, OmniFlash image modes and first/last/video/audio inputs still lack the required primary contract and exact live evidence in this release.
- PR188 remains draft; no CI status or merge is claimed.

## Safe continuation

Restore a stable authorized SSH connection, or obtain user approval to use the host's logged-in web console. Re-run only GET-based delivery proof; then create the isolated canary, verify free rejection/accounting, and submit at most one durable CNY2.4-ceiling canary intent within the original CNY50 budget. Only after exact settled wallet/media proof may the reviewed backup/drain/promote flow run. Never resubmit an existing or uncertain upstream task.

Private server receipts remain under `/opt/ai-api-stack/backups/nody-multimodal-186-20261007`; credentials and signed result URLs are not included here.
