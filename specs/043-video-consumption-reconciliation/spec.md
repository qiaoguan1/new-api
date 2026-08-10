# Feature Specification: Video Consumption Reconciliation

**Feature Branch**: `codex/issue-43-video-consumption`
**Created**: 2026-08-10
**Issue**: #43

## Goal

Collect authenticated Toonflow and Paisio video usage evidence, reconcile it with durable relay
jobs, and publish an internal provider view plus a provider-neutral public model-health view.
Downstream sale price remains Ark official price multiplied by 1.5; provider cost is comparison
evidence only and never drives video sale pricing.

## User Stories

### P1 - Trustworthy actual-cost collection

An operator can see the prior Beijing day's completed upstream video tasks and actual deductions.
Failed, refunded, incomplete, duplicated, or unauthenticated records never become actual cost.

### P2 - Relay-to-provider reconciliation

An operator can match gateway jobs to provider records by provider task identity. A unique bounded
model/time match may be reported separately as inferred; ambiguous records remain unmatched.

### P3 - Safe monitoring projections

Operators see provider task counts, success rate, evidence coverage, cost and last fetch time.
Ordinary users see only stable model names, availability and success rate.

## Requirements

- Toonflow collection MUST use an operator-provided authenticated web token. Cron MUST NOT solve or
  bypass CAPTCHA and MUST fail closed when the token is absent or expired.
- Paisio collection MUST use its authenticated account APIs and preserve provider task identifiers
  when present.
- All day boundaries MUST use `Asia/Shanghai` regardless of server timezone.
- Actual cost MUST be derived only from complete authenticated provider records and stored
  separately from `relay_sale_cny` and provider catalog/list price.
- Deduplication MUST use provider plus upstream task identifier before aggregation.
- Exact task-ID matches MUST take precedence. Inferred matches MUST be unique inside the configured
  time window; otherwise evidence status is `unknown`.
- Rerunning the same day MUST replace that day's deterministic snapshot without double counting.
- Public projections MUST omit provider identifiers, credentials, actual cost, sale amount and
  margin fields.
- Rolldek, PackAPI, Unity2, CLR and non-video pricing are out of scope.

## Success Criteria

- All unit tests cover Beijing day boundaries, duplicate records, completed/failed/refunded tasks,
  exact and ambiguous reconciliation, and public redaction.
- Every reported cost row identifies an authenticated evidence source and collection timestamp.
- Missing Toonflow authentication produces `incomplete` with null actual cost, never zero.
- Ten production read-only verification rounds show stable snapshots and no secret/public leakage.
