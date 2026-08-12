# Implementation Plan: Audited Video Multi-Channel Routing and Safe Auth Refresh

**Branch**: `codex/issue-63-video-multichannel-auth-refresh` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

## Summary

Bring the production-only v2.1 gateway release back into the repository, add exact billing collectors and eligibility gates for every reviewed video adapter, then add a separate credential lifecycle utility and scheduled runner. Production activation remains fail-closed and uses copied SQLite/credential fixtures before any route is enabled.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Python standard library HTTP/SQLite/threading; existing NewAPI notification endpoint  
**Storage**: SQLite gateway ledger plus root-owned 0600 credential/state JSON files  
**Testing**: `unittest`, compilation checks, disposable Docker image on production host, read-only smoke checks  
**Target Platform**: Linux server with Docker and cron/systemd  
**Project Type**: Sidecar gateway and operations scripts  
**Performance Goals**: Refresh/eligibility work off request path; task billing query bounded by configured timeout  
**Constraints**: No paid verification calls, no secret output, no CAPTCHA bypass, existing task identity and settlement semantics preserved  
**Scale/Scope**: Reviewed adapters `paisio`, `rolldek`, `toonflow`; PackAPI/Unity2 excluded

## Constitution Check

- GitHub Issue #63 exists, is on Project #3 and is In Progress.
- Work occurs on a dedicated non-main branch.
- Behavioral changes are test-first and security-sensitive changes receive explicit review.
- Secrets are injected only at deployment and are never committed.
- Production changes require recoverable backups and rollback containers/configs.

## Project Structure

```text
ops/video-job-gateway/
├── app.py
├── billing_collectors.py
├── credential_lifecycle.py
├── catalog.json
├── store.py
└── tests/
    ├── test_billing_collector.py
    ├── test_credential_lifecycle.py
    └── test_gateway_fallback.py

ops/channel-monitor/
├── scripts/
│   └── refresh-video-provider-auth.py
└── tests/
    └── test_refresh_video_provider_auth.py

specs/063-video-multichannel-auth-refresh/
├── spec.md
├── plan.md
├── research.md
├── tasks.md
└── verification.md
```

**Structure Decision**: Extend the existing Python sidecar and channel-monitor operations layer; do not add a new network service or expose a credential endpoint publicly.

## Deployment Strategy

1. Inventory provider capability and credential readiness without printing secrets.
2. Run tests against fake provider responses and copied SQLite data.
3. Back up the current gateway image, environment, source and SQLite using the online backup API.
4. Deploy refresh monitoring dark, verify it performs no credential mutation outside configured refresh windows.
5. Enable only providers whose generation and exact-billing probes both pass.
6. Run ten read-only rounds covering readiness, route catalog, settlement invariants, Webhook backlog and restart persistence.
7. Retain the prior container and configuration until the observation window completes.

