# Verification

## Automated tests

- 14 Python selector and fail-closed patcher tests passed.
- `go test ./setting/ratio_setting` passed in the Go 1.26.1 Alpine build
  environment used for the production candidate.
- `git diff --check` passed.

## Production pricing result

- The 2026-07-23 Beijing business-day run discovered 809 models from current
  enabled-channel configuration.
- 12 models were safely applied: eight from target-day actual deductions and
  four from recent actual deductions. The other 797 retained their existing
  prices: 785 had no trusted model-level actual cost in the window and 12 had
  no healthy enabled channel.
- Every applied decision was independently checked for the invariant customer
  price equals trusted actual cost times 1.5.
- The only semantic database change from the pre-deployment snapshot was
  `grok-imagine-video-1.5-fast`, whose base fixed price became 1.68. The three
  other newly covered fixed-price models already matched their computed values.
- `gpt-5.6-sol` now exposes and bills the explicit completion ratio 6 instead
  of the family fallback 8. The deployed candidate image is
  `new-api-fixed:completion-override-20260724-182800`.

## Ten-round validation

Ten consecutive server rounds passed status and pricing HTTP checks, database
option equality, all 12 markup invariants, monitoring totals, all nine required
containers, the deployed image, Beijing cron entries, and the unauthenticated
WeChat Pay route boundary.

Ten consecutive external-edge rounds also passed: the monitor returned 401
without credentials and 200 with valid Basic Auth, while public TCP 8791
remained unreachable.

`gpt-4o-mini` is one of the 12 valid pricing decisions because it is explicitly
configured on enabled channel 41. It is not in the anonymous pricing response
because its sole ability belongs to the non-public video group; it was verified
directly in the option database instead of being incorrectly removed from the
configured inventory.
