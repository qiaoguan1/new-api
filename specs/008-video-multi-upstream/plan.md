# Implementation Plan: Toonflow and Paisio Video Routing

**Branch**: `codex/issue-42-video-multi-upstream` | **Date**: 2026-08-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-video-multi-upstream/spec.md`

## Summary

Bring the production video gateway source under version control, replace single-route selection
with a deterministic equal-share route plan for eligible Toonflow and Paisio capabilities, persist
that plan before submission, and allow a single safe fallback only for definitive pre-creation
failures. Existing public names, pricing, delivery, and idempotency remain unchanged.

## Technical Context

**Language/Version**: Python 3.11 in the gateway container; repository tooling also supports Python 3.12

**Primary Dependencies**: Python standard library HTTP server, urllib transport, dataclasses, SQLite

**Storage**: Existing SQLite WAL gateway database with additive columns and in-place migration

**Testing**: Python unittest with fake adapters/transports and temporary SQLite databases

**Target Platform**: Linux Docker service on the relay host

**Project Type**: Internal web service colocated with NewAPI operations code

**Performance Goals**: Route selection under 5 ms; no additional upstream call on normal submission

**Constraints**: No duplicate upstream creation; credentials only from environment; public protocol
unchanged; bounded provider list; production state migration must be reversible

**Scale/Scope**: Two providers, seven stable model/resolution combinations, hundreds of active or
retained gateway jobs

## Constitution Check

- **Reviewed Protocol and Catalog**: PASS. Only existing stable names and authenticated provider
  models are added; provider names remain internal.
- **Evidence-First Billing**: PASS. Routing does not change cost evidence or relabel sale quotes.
- **Test-First Safety**: PASS. Selection, migration, persistence, restart, and fallback tests precede
  implementation.
- **Durable Multi-Provider Routing**: PASS. The complete route plan is persisted before submission;
  ambiguous outcomes never advance to another provider.
- **Credential Isolation and Observability**: PASS. Config files contain variable names only and
  public snapshots remain provider-neutral.

## Project Structure

### Documentation (this feature)

```text
specs/008-video-multi-upstream/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── route-decision.md
└── tasks.md
```

### Source Code (repository root)

```text
ops/video-job-gateway/
├── adapters.py
├── app.py
├── catalog.json
├── catalog.py
├── compose.yaml
├── Dockerfile
├── env.example
├── relay-pricing.json
├── relay_pricing.py
├── routing.py
├── store.py
└── tests/
    ├── test_gateway_routing.py
    ├── test_routing.py
    └── test_store_routes.py
```

**Structure Decision**: Preserve the deployed standalone Python gateway as an operations service
under `ops/video-job-gateway/`. This makes the production artifact reviewable without coupling its
state machine to NewAPI's Go request distributor.

## Complexity Tracking

No constitution violations require justification.
