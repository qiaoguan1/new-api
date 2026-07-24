# Production Verification

## Automated checks

- 13 Python unit tests passed for policy thresholds, stale data, balance
  freshness, active-only aggregation, patch refusal, idempotency, and internal
  UI states.
- All Python deployment artifacts compiled and `git diff --check` passed.
- The updated production-host verifier passed SSH, UFW, Fail2ban, private 8791,
  Nginx reachability, Basic Auth readability, and public service rules.

## Production evidence

- Rollback directory:
  `/opt/ai-api-stack/backups/issue28-monitor-health-20260724-175133`
- The rollback manifest verifies the original generator, monitor data,
  upstream definitions, root crontab, internal UI files, and pre-fix Basic Auth
  file.
- Hourly generation remains `0 * * * *` under `CRON_TZ=Asia/Shanghai`; the
  08:20, 08:30, and 08:40 daily pricing pipeline was unchanged.
- All 11 enabled channels contribute to global totals. Ten match configured
  upstreams; the remaining Topaz channel is visible as one configuration
  warning rather than omitted.
- Nine retired upstreams report `inactive` and do not increment alerts.
- NewAPI automatic all-channel testing remains unset.
- Anonymous monitor access returns 401; valid Basic Auth returns 200; public
  TCP 8791 is unreachable.

## Ten final rounds

Ten consecutive external rounds each passed:

- NewAPI home: HTTP 200
- Channel Monitor: anonymous 401, authenticated JavaScript/data 200
- Retired Sub2API hostname: HTTP 410
- Health totals: 2 actionable alerts, 1 configuration warning, coverage 10/11

All nine production containers remained running, and the host-hardening suite
passed after the access-permission correction.
