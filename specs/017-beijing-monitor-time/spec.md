# Feature Specification: Beijing-Time Monitoring and Billing Days

**Issue**: [#17](https://github.com/qiaoguan1/new-api/issues/17)

## User Outcome

Operators see and process one unambiguous Beijing business day across upstream
billing, local usage reconciliation, channel audit, automatic-pricing inputs,
monitor artifacts, cron execution, and customer-facing update times.

## Functional Requirements

- **FR-001**: The business timezone MUST be the IANA timezone
  `Asia/Shanghai`, independent of the host operating-system timezone or the
  server's physical location.
- **FR-002**: The default target day MUST be the previous complete Beijing
  calendar day. `CHANNEL_MONITOR_DAY=YYYY-MM-DD` MUST retain its override
  behavior and represent that same Beijing business-day contract.
- **FR-003**: Classic NewAPI upstream epoch timestamps MUST be assigned to a
  day after conversion to `Asia/Shanghai`; the v1 usage API MUST receive the
  same Beijing calendar date as both `start_date` and `end_date`.
- **FR-004**: Local PostgreSQL audit and daily-business queries MUST partition
  `logs.created_at` with `AT TIME ZONE 'Asia/Shanghai'`.
- **FR-005**: Fetch, audit, and monitor ISO timestamps MUST contain an explicit
  `+08:00` offset. Epoch timestamps remain absolute Unix seconds.
- **FR-006**: Both the signed-in model-status page and the protected internal
  monitor MUST label and render wall-clock timestamps in Beijing time.
- **FR-007**: Production cron MUST declare `CRON_TZ=Asia/Shanghai` before the
  monitor jobs. Hourly materialization and the 08:20, 08:30, and 08:40 jobs
  MUST retain their existing wall-clock schedules.
- **FR-008**: Time conversion, missing timezone data, or invalid day overrides
  MUST fail closed before audit or automatic-pricing data can be treated as
  complete.
- **FR-009**: Existing cost, markup, channel-health, and atomic pricing rules
  MUST remain unchanged.

## Acceptance Scenarios

1. At `2026-07-23T16:30:00Z` (00:30 on July 24 in Beijing), the previous
   complete business day is `2026-07-23`, not `2026-07-22`.
2. Epoch events at `2026-07-22T16:00:00Z` and just after it belong to Beijing
   business day `2026-07-23`; an event one second earlier belongs to July 22.
3. Audit and daily-business SQL for `2026-07-23` select exactly the rows whose
   Beijing-local date is July 23.
4. A browser in any local timezone displays the model-page refresh timestamp
   with an explicit Beijing-time label.
5. Production artifacts for one workflow all identify the same business date
   and use ISO timestamps ending in `+08:00`.

## Non-Goals

- Do not alter the 1.5x customer-price contract or any pricing threshold.
- Do not convert rolling 24-hour performance windows into calendar-day
  windows.
- Do not assume upstream hosts are physically located in China; the business
  contract, not host geography, determines the timezone.
- Do not expose internal channel, upstream, cost, or profit data to customers.

## Observed Baseline

For production date `2026-07-23`, the existing UTC partition selected 327 log
rows while the Beijing partition selected 332. This proves the current UTC and
requested Beijing business-day boundaries are materially different.
