# Feature Specification: Upstream Balance Email Alerts

**Feature Branch**: `codex/issue-56-upstream-balance-email-alerts`  
**Created**: 2026-08-11  
**Issue**: #56

## Goal

Detect exhausted balances for every enabled, credentialed upstream through lightweight authenticated
balance probes and notify the operator at `961246161@qq.com` without waiting for the daily billing
reconciliation.

## User Scenarios

### P1 - Learn that an upstream has no usable balance

Every hour the relay probes the provider's account-balance endpoint. A finite, trustworthy balance at
or below the configured threshold creates one depletion email with the provider display name, raw
account balance, threshold, and Beijing timestamp.

### P1 - Avoid false or repeated alarms

Missing fields, login failures, timeouts, malformed JSON, and expired Toonflow authorization remain
`unknown`; none is converted to zero. A continuing depletion is deduplicated and only reminded after
the configured interval.

### P1 - Learn that service can resume

After a delivered depletion alert, the first trustworthy balance above the threshold sends one
recovery email. Further healthy checks are silent.

### P2 - Detect a blind monitor

Three consecutive probe failures create one separate collection-failure email. Recovery of the probe
sends one monitor-recovery email. These events never claim that the account balance is zero.

## Functional Requirements

- **FR-001**: Probe only upstreams that are enabled in `upstreams.json` and have a credential object.
- **FR-002**: Reuse the existing classic NewAPI, v1 usage, and Toonflow authenticated balance APIs,
  but do not fetch logs, price catalogs, task lists, or make paid model calls.
- **FR-003**: Persist a separate live-balance snapshot; do not overwrite the daily actual-cost ledger.
- **FR-004**: Treat only finite numeric balances as trustworthy. Unknown balances MUST NOT trigger a
  depletion or recovery event.
- **FR-005**: The global threshold defaults to `0` in the provider's raw account unit and is
  configurable without a code change.
- **FR-006**: Persist per-provider health, failure count, last delivered event, and reminder time in an
  atomically replaced private state file.
- **FR-007**: Depletion, recovery, collection-failure, and collection-recovery transitions MUST be
  deduplicated across process restarts. Failed email delivery MUST remain retryable.
- **FR-008**: All user-facing timestamps MUST use Asia/Shanghai.
- **FR-009**: The monitor MUST call a root-authenticated NewAPI notification endpoint using the
  existing mode-0600 root access-token file. NewAPI MUST send through its existing SMTP
  configuration to the operator address fixed by `UPSTREAM_BALANCE_ALERT_EMAIL`.
- **FR-010**: Source, logs, snapshots, and email bodies MUST NOT contain usernames, passwords,
  cookies, Bearer tokens, SMTP authorization codes, or raw upstream exception bodies.
- **FR-011**: The notification endpoint MUST accept only bounded structured balance-event fields;
  callers cannot choose a recipient, SMTP setting, subject, or arbitrary HTML.
- **FR-012**: `--dry-run` MUST report planned event types without sending mail or mutating alert
  state. `--test-email` MUST validate delivery without changing provider alert state.
- **FR-013**: The production schedule MUST use `CRON_TZ=Asia/Shanghai`, a non-overlapping lock, and
  one hourly run.

## Success Criteria

- Automated tests cover classic/v1/Toonflow balance-only probes, unknown-vs-zero, threshold edges,
  deduplication, reminders, recovery, three-failure escalation, root notification authentication,
  recipient isolation, and atomic state.
- The full channel-monitor suite remains green.
- Ten deterministic dry-run rounds produce stable events without any paid provider request.
- Production read-back proves the hourly job, private file permissions, and live snapshot are correct.

## Out of Scope

- Changing pricing, video routing, CLR, account credentials, or public monitoring fields.
- Automatically buying or recharging provider credit.
