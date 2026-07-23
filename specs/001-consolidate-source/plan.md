# Implementation Plan: Consolidate Server Source

**Branch**: `codex/issue-1-consolidate-server-source` | **Date**: 2026-07-23 | **Spec**: `specs/001-consolidate-source/spec.md`
**Input**: Feature specification from `/specs/001-consolidate-source/spec.md`

## Summary

Consolidate the server's active New API customizations into `/root/new-api-build/new-api`, preserving all existing canonical worktree changes and selectively integrating the validated WeChat Pay and compatibility work from `wechatpay-final`. The work is test-first, excludes backup artifacts, and stops before deployment or any production container change.

## Technical Context

**Language/Version**: Go 1.25.1; TypeScript with React 19
**Primary Dependencies**: Gin, GORM, `wechatpay-go` v0.2.21, Bun workspace tooling
**Storage**: Existing SQLite/MySQL/PostgreSQL-compatible GORM models
**Testing**: Go `testing`; frontend type-check and production build
**Target Platform**: Debian 12, Dockerized Linux runtime
**Project Type**: Go API plus React web application
**Performance Goals**: No regression to existing request paths; bounded WeChat order polling
**Constraints**: Preserve project identity and all pre-existing worktree edits; no secrets; no deployment/restart
**Scale/Scope**: 25 functional candidate paths, with 3 backup artifacts explicitly excluded

## Constitution Check

*GATE: Must pass before implementation and again after design.*

- **Canonical source and traceability**: PASS — one canonical repository, Issue #1, feature spec, explicit path decisions.
- **Project identity and compatibility**: PASS — no rebranding; route and database compatibility are preserved.
- **Test-first evidence**: PASS — tests are imported and executed before their implementations.
- **Secret-free production safety**: PASS — no credentials enter Git; no production service mutation is in scope.
- **Minimal, reviewable changes**: PASS — only classified functional paths are integrated; backups are excluded.

Post-design re-check: PASS. The API contract extends existing routes without removing legacy behavior, and the data model reuses the current top-up lifecycle.

## Project Structure

### Documentation

```text
specs/001-consolidate-source/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── wechatpay-api.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
controller/                 # Top-up and WeChat Pay handlers/tests
model/                      # Top-up persistence and idempotency/tests
router/                     # API route registration
setting/                    # WeChat Pay and payment configuration/tests
web/default/src/features/  # Wallet and channel UI integrations
go.mod, go.sum              # WeChat Pay SDK dependency
```

**Structure Decision**: Retain the existing repository layout. New WeChat Pay code lives beside the existing top-up code; frontend behavior remains inside wallet features.

## Reconciliation Strategy

1. Record the canonical/final path comparison and explicit exclusions.
2. Copy only new tests from `wechatpay-final`; run focused tests and retain the expected red evidence.
3. Integrate reviewed backend implementation and dependency files; correct test fixtures rather than weakening production validation.
4. Integrate reviewed frontend and compatibility paths.
5. Run focused tests, full backend tests, frontend checks, artifact/secret scans, and verify production container state is unchanged.
6. Review, commit, push, and open a pull request linked to Issue #1.

## Complexity Tracking

No constitution violations requiring justification.
