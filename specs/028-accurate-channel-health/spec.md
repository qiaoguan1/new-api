# Accurate Hourly Channel Health

**Issue**: [#28](https://github.com/qiaoguan1/new-api/issues/28)

## Goal

Make the hourly internal Channel Monitor describe current enabled-channel
health without treating retired channels, stale tests, or unknown balances as
live incidents.

## Requirements

1. Aggregate calls, successes, errors, latency, cost, balance, and recent
   errors from enabled channels only.
2. Classify upstreams with no enabled channels as `inactive`; they are neither
   alerts nor warnings.
3. Treat a channel test as fresh for two hours. An enabled upstream with no
   traffic and no fresh test is `stale`, reported as a warning rather than a
   service incident.
4. Require at least ten calls, five failures, and a 20% failure rate before
   classifying traffic as `error`.
5. Use database balance for health only when its recorded update is at most 24
   hours old. A zero with no update timestamp is unknown, not `low_balance`.
6. Keep hourly aggregation and daily pricing schedules in Asia/Shanghai.
7. Do not enable NewAPI's automatic all-channel test because it can execute
   billable image/video generations and auto-disable channels.
8. Include every enabled channel in global totals. Surface enabled channels
   that do not match an upstream definition as configuration warnings instead
   of silently omitting their traffic.
9. Verify both sides of the monitor access boundary: anonymous requests return
   401 and valid Basic Auth can read the page. The credential file remains
   root-owned and readable only by the Nginx worker group.

## Safety boundary

This change does not update credentials, channel status, billing ratios,
prices, user quota, or automated pricing inputs.
