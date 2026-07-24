# Model-only Channel Health

**Issue**: [#32](https://github.com/qiaoguan1/new-api/issues/32)

## Requirements

1. `/channel-health` requests only model performance data.
2. The page displays model name, request count, success rate, average latency,
   and output speed.
3. It does not request or render channel names, upstream names, balances,
   revenue, costs, margins, or upstream errors.
4. The page states the correct hourly aggregation cadence and retains manual
   refresh plus 24-hour, 7-day, and 30-day ranges.
5. Deployment has a restricted rollback bundle and ten production regression
   rounds.
