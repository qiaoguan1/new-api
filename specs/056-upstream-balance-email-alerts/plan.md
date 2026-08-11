# Implementation Plan: Upstream Balance Email Alerts

## Technical Context

- Python 3, `requests`, standard-library `smtplib`/`email`, JSON state, cron.
- Existing authenticated adapters in `ops/channel-monitor/scripts/fetch-upstream-balance.py`.
- Production server runs in the United States but all operator time is Asia/Shanghai.

## Design

1. Add a balance-only probe to the existing collector. It logs in and reads only the account-self or
   Toonflow points-preview endpoint, with classic-to-v1 fallback and the existing HTTPS/host guards.
2. Add `monitor-upstream-balances.py` to select enabled credentials, write a private live snapshot,
   calculate durable state transitions, and submit structured notifications with the existing
   mode-0600 NewAPI root access token.
3. Add a RootAuth-protected NewAPI endpoint that accepts only an allowlisted event kind and bounded
   provider/balance/time fields. It constructs the subject/body internally and sends to the fixed
   `UPSTREAM_BALANCE_ALERT_EMAIL` through the already configured SMTP service.
4. Run the new monitor once per hour under `flock`; retain the existing daily full balance/cost
   collection unchanged.

## State Model

- Balance state: `healthy`, `depleted`, or `unknown`.
- A finite balance `<= threshold` is depleted; a finite balance above it is healthy.
- Unknown increments `consecutive_failures`; the third failure becomes a collection-failure event.
- Delivery timestamps are updated only after SMTP success. Therefore a failed send retries next run.
- Depleted reminders default to 24 hours. Recovery is emitted only when the corresponding alert was
  previously delivered.

## Security and Rollback

- No secret values in CLI arguments, source, Git, JSON snapshots, email, or logs.
- Refuse group/world-readable NewAPI access-token files on POSIX.
- The HTTP request cannot override recipient, SMTP settings, subject, or arbitrary HTML.
- Back up changed production scripts and crontab before activation.
- Rollback restores the timestamped script/crontab backup and removes only the new cron line.
