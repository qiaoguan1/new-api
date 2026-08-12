# Tasks: Audited Video Multi-Channel Routing and Safe Auth Refresh

## Phase 1: Production and code inventory

- [x] T001 Trace all reviewed adapter, catalog, credential and billing paths.
- [x] T002 Record sanitized provider readiness and supported refresh semantics in `research.md`.
- [x] T003 Import the deployed v2.1 billing/Webhook gateway changes into the tracked source without credentials or runtime data.

## Phase 2: Provider eligibility and settlement

- [x] T004 Write failing tests that exclude providers lacking exact billing readiness.
- [x] T005 Implement generation + billing + health eligibility for new routes.
- [x] T006 Write failing collectors tests for every reviewed provider ledger API.
- [x] T007 Implement exact task-level collectors with unique terminal record checks.
- [x] T008 Verify deterministic priority, definite-rejection fallback and uncertain-outcome fail-close.

## Phase 3: Credential lifecycle

- [x] T009 Write failing tests for expiry parsing, refresh windows, atomic 0600 replacement, failure preservation and CAPTCHA-bound warnings.
- [x] T010 Implement provider credential lifecycle and sanitized status.
- [x] T011 Implement scheduled refresh/readiness runner and deduplicated notifications.
- [x] T012 Document operator replacement and rollback procedures.

## Phase 4: Review and deployment

- [x] T013 Run complete gateway/channel-monitor tests and compilation checks.
- [x] T014 Complete comprehensive and security reviews; address every finding.
- [ ] T015 Commit, push, open PR, verify CI and merge to the active production base.
- [ ] T016 Back up and deploy gateway/refresh schedule with only fully eligible providers enabled.
- [ ] T017 Run ten read-only production verification rounds and record results in `verification.md`.
