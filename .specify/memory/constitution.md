<!--
Sync Impact Report
- Version change: template -> 1.0.0
- Added principles: Preserve Customized Production Invariants; Selective Upstream Adoption;
  Test-First Observable Behavior; Cross-Platform and Billing Safety; Reversible Delivery
- Added sections: Technical and Safety Constraints; Development and Review Workflow
- Templates updated: .specify/templates/plan-template.md, spec-template.md, tasks-template.md
- Follow-up TODOs: none
-->
# XingTu New API Constitution

## Core Principles

### I. Preserve Customized Production Invariants
Every change MUST preserve XingTu's reviewed routing, stable model mappings, pricing, channel
monitoring, video settlement, security boundaries, and public API contracts unless a separate issue
explicitly authorizes changing that invariant. Upstream code MUST NOT replace a customized behavior
merely because it is newer. The rationale is that production correctness and audited business rules
take precedence over upstream parity.

### II. Selective Upstream Adoption
Every upstream commit in an upgrade scope MUST receive a recorded disposition: adopt, manual-port,
already-equivalent, defer, or reject-with-conflict. Adopted code MUST retain upstream provenance.
Bulk merges of upstream branches are prohibited for selective upgrades. This makes omissions,
conflicts, and later upgrades auditable.

### III. Test-First Observable Behavior
Behavioral changes MUST follow RED-GREEN-REFACTOR: add or identify a meaningful regression test,
observe the failing or missing behavior, apply the minimum compatible change, and run the relevant
suite. Tests MUST protect user-visible contracts, routing, accounting, compatibility, or recovery;
tests that only assert implementation details are prohibited.

### IV. Cross-Platform and Billing Safety
Backend changes MUST remain compatible with SQLite, MySQL, and PostgreSQL and preserve all request,
relay, quota, refund, and settlement invariants in `AGENTS.md`. Optional request fields MUST preserve
explicit zero values. No adopted change may weaken bounded multipliers, saturating quota conversion,
credential redaction, or provider protocol correctness.

### V. Reversible Delivery
Each upgrade batch MUST be independently reviewable, testable, and revertible. Production deploys,
paid upstream requests, credential reads, and production database writes are outside an upgrade PR
unless separately authorized. Conflicting or uncertain changes MUST remain unapplied and documented
rather than resolved by assumption.

## Technical and Safety Constraints

- The existing Go, React, TypeScript, Bun, GORM, Redis, and supported database architecture remains
  authoritative.
- Project and author identity protected by `AGENTS.md` MUST remain unchanged.
- Repository-native JSON wrappers, database locking helpers, quota math, i18n, and provider relay
  conventions are mandatory.
- Upstream comparison evidence MUST come from immutable commit identifiers and a recorded merge base.
- Secrets, authenticated response bodies, copied production databases, and private provider evidence
  MUST NOT enter specifications, logs, commits, issues, or review artifacts.

## Development and Review Workflow

1. Work starts from a tracked GitHub issue in the correct project and an isolated `codex/` branch.
2. A specification defines scope, safety boundaries, classifications, and measurable acceptance.
3. Research records the upstream range, provenance, overlap, conflicts, and selected batch.
4. Tests precede each behavioral adoption; relevant backend, frontend, and custom operations suites
   run before review.
5. Comprehensive review evaluates blind spots, consistency, maintainability, security, performance,
   documentation, and style. Every finding is fixed or tracked.
6. PR and CI gates MUST pass before merge. Production deployment remains a separate authorized step.

## Governance

This constitution governs Spec Kit artifacts and upgrade implementation. Amendments require an
explicit issue, rationale, template impact review, and semantic version bump. MAJOR removes or
redefines a principle, MINOR adds a principle or materially expands governance, and PATCH clarifies
without changing obligations. Every plan and code review MUST verify compliance; `AGENTS.md` remains
the authoritative runtime coding guide where it is stricter.

**Version**: 1.0.0 | **Ratified**: 2026-08-24 | **Last Amended**: 2026-08-24
