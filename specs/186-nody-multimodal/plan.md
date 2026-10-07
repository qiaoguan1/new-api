# Implementation Plan: Nody multimodal video relay

**Branch**: `codex/issue-186-nody-multimodal` | **Date**: 2026-10-07 | **Spec**: [spec.md](spec.md)

## Summary

Inspect the current provider-owned API attachment, derive per-model mode contracts, then extend existing public/gateway/adapter boundaries with exact tests and bounded real validation. Preserve text generation and actual-cost settlement; never advertise modes solely by deleting guards.

## Technical Context

**Languages**: Go public frontdoor; Python video gateway.
**Dependencies**: existing Gin/GORM/decimal and gateway transport/media-verifier modules.
**Storage**: existing PostgreSQL public task/wallet ledger and SQLite gateway task store.
**Testing**: Go package tests; Python unittest; isolated canary and authorized CNY50 maximum live evidence.
**Target Platform**: existing Linux Docker production stack on 156.239.3.210.
**Constraints**: no extra recharge, secret exposure, uncertain-task replay, unrelated data or policy mutation.
**Scope**: seven connected Nody video model IDs; only documented and verified new input modes.

## Constitution Check

- Follow user scope; stop at new authority or unavailable provider-contract decisions.
- Existing working tree was clean; changes use issue186 branch with project status In Progress.
- Tests fail before implementation; comprehensive and security review precede rollout/PR.
- Keep authentication, transactional billing, task isolation and no-replay invariants.

## Project Structure

```text
specs/186-nody-multimodal/   # spec, plan, tasks, provider research and verification
cmd/public-video/           # public request validation, reserve and discovery
ops/video-job-gateway/      # internal mode/role contract, Nody adapter, media and pricing
```

## Execution Gates

1. Primary provider document and current authorized metadata establish parameter and price evidence.
2. Add exact failing contract/asset/billing tests, implement minimal confirmed routes and keep others disabled.
3. Validate local tests, isolated canary, real async result/cost proof within tracked budget.
4. Review/security findings, private backup and controlled deploy; verify public discovery and legacy text.

No production capability or price change is authorized by a passing mock alone.
