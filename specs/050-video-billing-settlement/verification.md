# Verification and Rollback: Video Billing Settlement

Date: 2026-08-11 (Asia/Shanghai)

## Verified behavior

- Ark official request cost is calculated first and multiplied by 1.5 once at the final quota boundary.
- Dynamic provider marketplace prices and the 0.15 group ratio cannot override a supported video reservation.
- Successful tasks remain `settlement_pending` and withhold the result until exact, authenticated provider-task evidence is accepted.
- Exact final charge is provider net cost multiplied by 1.5; refund and supplement deltas are transactional and idempotent.
- A positive ordinary wallet may cross zero for one accepted task; later requests are blocked until recharge restores a positive balance.
- Finite tokens and subscriptions never overdraft.
- Failed tasks refund the reservation exactly once.
- Public task and monitor payloads omit provider identity, upstream task/model identifiers, actual provider cost, margin, and markup.

## Local gates

- `go test ./... -count=1`: passed for every package.
- `go vet ./controller ./dto ./model ./relay ./router ./service ./types`: passed.
- `python -m compileall -q ops/channel-monitor ops/video-job-gateway`: passed.
- Gateway tests: 26 passed per round.
- Channel-monitor tests: 166 passed per round.
- Ten deterministic rounds of focused Go plus both Python suites: 10/10 passed.
- `gofmt -d` over all changed and new Go files: clean.
- `git diff --check`: clean.
- Go race instrumentation could not run locally because this Windows runtime has `CGO_ENABLED=0` and no GCC. Transactional concurrency and replay tests passed; CI/Linux race should be used if available.

## Production verification sequence

1. Back up the database, active compose configuration, service environment, and currently deployed image digests.
2. Deploy the NewAPI migration and application image before enabling the settlement publisher.
3. Deploy the gateway and channel-monitor publisher with tokens stored only in root-owned mode-0600 files.
4. Submit one test request for each supported stable video SKU/resolution and verify the reservation against the approved Ark table.
5. Verify Paisio is attempted first and Toonflow only after a retryable Paisio submission failure.
6. Let each task reach success, publish exact evidence, and verify the wallet delta equals `ceil(actual_cost_cny_exact × 1.5 × 500000)` quota.
7. Verify duplicate evidence is a replay, conflicting evidence fails closed, and missing evidence keeps the result withheld.
8. Verify public task and monitoring responses contain no provider/cost/margin fields.
9. Verify failed-task refund, wallet debt gate/recharge recovery, finite-token rejection, and subscription rejection.
10. Observe logs and pending-settlement counts through one scheduled settlement cycle before declaring production complete.

## Rollback

1. Disable the settlement publisher first so no new evidence is posted during rollback.
2. Restore the previous NewAPI image and gateway service while retaining the append-only settlement table and task records.
3. Restore the pre-deployment database backup only if the migration itself prevents the previous binary from starting; otherwise keep the newer database to preserve billing audit history.
4. Restore the prior monitor schedule/configuration and verify normal non-video traffic.
5. Export every still-pending video task before rollback and reconcile it manually; never fabricate or silently finalize missing-cost evidence.
