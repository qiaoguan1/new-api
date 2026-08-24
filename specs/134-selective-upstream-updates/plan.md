# Implementation Plan: Selective Upstream Updates

**Branch**: `codex/issue-134-selective-upstream` | **Date**: 2026-08-24 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/134-selective-upstream-updates/spec.md`

## Summary

Freeze and classify all 97 upstream commits missing from fork `main`, then deliver one reversible
first batch containing only the OAuth callback-mode security fix and the DOMPurify patch update.
Every other commit remains unchanged with a recorded reason.

## Technical Context

**Language/Version**: Go 1.22+, TypeScript with React 19, PowerShell validation commands

**Primary Dependencies**: Gin, GORM, React, TanStack Router, Bun, node:test

**Storage**: No new runtime storage; checked-in Markdown audit artifacts only

**Testing**: Bun test, TypeScript typecheck, Oxlint, frontend production build, relevant Go and custom
operations regression suites

**Target Platform**: Linux relay server and browser administration UI; Windows development host

**Project Type**: AI API gateway with web administration frontend and custom operations sidecars

**Performance Goals**: No request-path performance regression; dependency-only update keeps bundle
build within the existing pipeline

**Constraints**: No bulk upstream merge, no production access, no billing/routing behavior changes,
and no credentials or paid requests

**Scale/Scope**: 97 audited upstream commits; two changes in the first implementation batch

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- [x] Customized production invariants are enumerated and preserved.
- [x] Every upstream commit in scope has a planned disposition and immutable provenance.
- [x] Behavioral adoptions have test-first coverage and observable acceptance criteria.
- [x] Database, relay, billing, quota, security, and i18n constraints are preserved.
- [x] The batch is independently reversible and excludes unauthorized production mutations.

## Project Structure

### Documentation (this feature)

```text
specs/134-selective-upstream-updates/
├── checklists/requirements.md
├── contracts/compatibility-ledger.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
├── upstream-compatibility.md
└── tasks.md
```

### Source Code (repository root)

```text
web/
├── package.json
├── bun.lock
└── src/
    ├── features/auth/lib/
    ├── features/profile/components/tabs/
    └── routes/oauth/

controller/ service/ model/ relay/ ops/
└── unchanged by the first batch; relevant suites provide regression evidence
```

**Structure Decision**: Keep all implementation in the existing frontend feature and route layout.
The compatibility ledger is feature documentation rather than a new runtime subsystem.

## Complexity Tracking

No constitution violations or new runtime abstractions are required.

## Delivery Phases

1. Freeze and validate the upstream inventory.
2. Record all compatibility dispositions and first-batch rationale.
3. RED: add the upstream OAuth callback-mode regression test and observe the missing behavior.
4. GREEN: manually port the OAuth logic and integrate the two existing call sites.
5. Apply the clean DOMPurify declaration update and regenerate its matching Bun lockfile.
6. Run focused tests, typecheck, lint, build, and affected custom regression suites.
7. Review, fix findings, publish the artifact, and merge only after CI is green.
