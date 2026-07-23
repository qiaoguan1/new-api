# Feature Specification: Privacy-Safe Model Status

**Issue**: [#15](https://github.com/qiaoguan1/new-api/issues/15)

## User Outcome

Every signed-in user can see whether each model is working without learning
which upstream or channel serves it, or seeing any internal commercial data.

## Acceptance Criteria

- All signed-in users can reach a `模型状态` page from the main sidebar.
- The page may show only model name, selected time window, request count,
  success rate, average latency, output speed, and the page update time.
- The user page does not request the root-only channel monitor endpoint.
- Channel IDs/names, upstream names/hosts, balances, revenue, upstream costs,
  gross profit/margin, credential state, and raw internal errors are absent from
  both the rendered page and every API response available to a normal user.
- Empty, loading, failure, and no-request states are understandable.
- The internal channel monitor and its commercial reconciliation remain
  available only through existing protected administrator paths.
- The local monitor materialization cron changes from every five minutes to
  hourly; 08:20 collection, 08:30 audit, and 08:40 pricing remain unchanged.
- Relevant frontend, Go, and Python verification passes before production
  deployment.

## Non-Goals

- Do not expose per-channel routing or vendor availability to customers.
- Do not change the daily cost collection, audit, pricing, or timezone policy.
- Do not generate synthetic or paid model probes for this page.
- Do not delete internal monitor data required by administrators.
