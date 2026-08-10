# Implementation Plan: Paisio-First Video Routing

**Branch**: `codex/issue-47-paisio-priority-routing` | **Date**: 2026-08-10 | **Spec**: [spec.md](spec.md)

## Summary

Replace equal-share routing for shared video capabilities with catalog-controlled fixed precedence:
Paisio priority 10, Toonflow priority 20. Keep the existing persisted route plan and definite-only
fallback state machine. Update private selection reason, documentation, and tests.

## Technical Context

**Language/Version**: Python 3.11 production; Python 3.12-compatible repository tests

**Primary Dependencies**: Python standard library, SQLite, existing provider adapters

**Storage**: Existing gateway SQLite schema; no migration

**Testing**: Python unittest with fake adapters and temporary databases

**Target Platform**: Linux Docker service on the relay host

**Project Type**: Internal video gateway service

**Performance Goals**: Route planning below 5 ms; no additional call on normal submission

**Constraints**: No duplicate generation, no credential output, public contract unchanged

**Scale/Scope**: Two providers and seven stable model/resolution combinations

## Constitution Check

- **Stable Protocol**: PASS; no downstream request or response changes.
- **Durable Routing**: PASS; existing plans remain immutable and persisted before submission.
- **Duplicate Prevention**: PASS; uncertain outcomes never advance.
- **Evidence and Pricing Isolation**: PASS; pricing and cost collection are untouched.
- **Credential Isolation**: PASS; no secret-bearing files change.

## Project Structure

```text
specs/047-paisio-priority-routing/
  spec.md
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/route-decision.md
  checklists/requirements.md
  tasks.md

ops/video-job-gateway/
  app.py
  catalog.json
  routing.py
  README.md
  tests/
```

**Structure Decision**: Reuse the reviewed standalone gateway and its catalog priority field.

## Complexity Tracking

No constitution violations.
