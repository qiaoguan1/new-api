# Implementation Plan: Video Consumption Reconciliation

## Summary

Add pure parsing/reconciliation helpers, extend the existing upstream collector for Toonflow's
authenticated web API and task-level Paisio evidence, then generate private and public monitor
snapshots from the gateway SQLite database and provider ledger.

## Technical Context

- Python 3.11+, `requests`, standard-library SQLite and `unittest`
- Production paths: `/opt/ai-api-stack/channel-monitor` and
  `/opt/xtai/state/video-job-gateway/data/video-jobs.sqlite3`
- Secrets remain in `/opt/ai-api-stack/channel-monitor/upstream-credentials.json`
- Production day boundary: `Asia/Shanghai`

## Constitution Check

- Reviewed Protocol: stable downstream model names only in the public projection.
- Evidence-First Billing: actual cost, sale amount and list price are separate nullable facts.
- Test-First Safety: parsing and reconciliation tests precede implementation.
- Durable Routing: reconciliation reads persisted gateway jobs and never resubmits them.
- Credential Isolation: token/password values never enter repository, logs or snapshots.

## Delivery

1. RED tests for provider parsing, dedupe, time boundaries, matching and redaction.
2. GREEN pure module and bounded authenticated adapters.
3. Integrate deterministic daily snapshot generation.
4. Back up production, dry-run the prior Beijing day, deploy exact reviewed commit.
5. Verify ten read-only rounds, post review artifact, merge and close issue.
