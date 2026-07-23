# Verification

## Local and Isolated Build Checks

- `python -m unittest discover -s ops/channel-monitor/tests -v`: 63 passed.
- `bun run typecheck`: passed.
- Targeted Prettier check: passed.
- Targeted ESLint check: passed.
- `bun run build`: passed.
- Go 1.26.1 isolated checks for `./controller`, `./pkg/perf_metrics`, and
  `./router`: passed.
- Full production Docker builds completed successfully for both the initial
  deployment and the final time-label revision.

## Production Checks

- Active image: `new-api-fixed:model-status-20260724-issue15-v2`.
- Container health: healthy after both deployments.
- Root crontab contains one hourly materialization job (`0 * * * *`) and no
  five-minute materialization job.
- Daily collection, audit, and pricing jobs remain at 08:20, 08:30, and 08:40.
- Manual monitor generation completed successfully and wrote fresh dashboard
  materialization data.
- Normal-user browser verification rendered the model-status page with model
  name, status, request count, success rate, average response time, and output
  speed; browser console errors: 0.
- The performance API returned exactly `model_name`, `avg_latency_ms`,
  `success_rate`, `avg_tps`, and `request_count` for each model.
- Anonymous and common-user requests could not retrieve internal channel
  monitor data.
- The temporary common-user access token was cleared after every verification.
- Ten final production verification rounds passed all health, HTTP, privacy,
  authorization, account cleanup, cron, and log checks.

## Recovery

- Rollback assets are stored at
  `/opt/ai-api-stack/backups/issue15-model-status-20260724-000751` with mode
  `0700`, including the original and first-deployment compose/source snapshots.
