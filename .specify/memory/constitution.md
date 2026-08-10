<!--
Sync Impact Report
- Version change: template -> 1.0.0
- Added principles: Reviewed Protocol, Evidence-First Billing, Test-First Safety,
  Durable Multi-Provider Routing, Credential Isolation and Observability
- Added sections: Operational Constraints, Delivery Workflow
- Templates: tasks-template.md updated; plan/spec templates remain compatible
- Deferred items: none
-->
# New API Relay Operations Constitution

## Core Principles

### I. Reviewed Protocol and Catalog
Every externally available model MUST resolve through a versioned, reviewed catalog to one
stable downstream name, supported capability set, and explicit upstream route. Unknown or
ambiguous upstream names MUST remain quarantined until reviewed. Provider-specific names
MUST NOT leak into the public contract.

### II. Evidence-First Billing
Upstream actual cost, downstream sale price, and calculated margin MUST be stored and reported
as separate facts. An actual cost MUST cite authenticated provider evidence or a reconciled
provider task record. Missing evidence MUST be represented as unknown; a catalog price,
official reference price, quote, or downstream charge MUST NOT be relabeled as actual cost.

### III. Test-First Safety (NON-NEGOTIABLE)
Behavioral changes MUST follow red-green-refactor: a focused test demonstrates the missing or
incorrect behavior before implementation, then the full relevant suite proves no regression.
Time boundaries, retries, refunds, partial failures, stale catalogs, and missing evidence MUST
have explicit tests where applicable.

### IV. Durable Multi-Provider Routing
Route selection MUST be deterministic, auditable, and persisted before submission. A request
MAY fall back to another provider only when the first provider has not created an upstream task.
After an upstream task may exist, retries MUST poll or reconcile that same task and MUST NOT
create a duplicate generation. Restarts MUST preserve provider choice and idempotency.

### V. Credential Isolation and Observability
Provider credentials MUST come from server-managed secrets and MUST never enter repositories,
public manifests, logs, monitoring pages, or user-visible errors. Operational records MUST expose
only the fields required to audit provider choice, task state, evidence coverage, and timestamps.
Public monitoring MUST hide internal provider names, margins, and credential metadata.

## Operational Constraints

- Production time windows and daily reconciliation use Asia/Shanghai boundaries regardless of
  server location.
- NewAPI database changes MUST remain compatible with SQLite, MySQL, and PostgreSQL when they
  touch shared application models.
- Provider adapters MUST use bounded timeouts, validated result hosts, size limits, and explicit
  retry classification.
- Production changes require a timestamped backup, dry-run or canary evidence, atomic activation,
  and post-write/read-back verification.
- CLR and unrelated services are outside scope unless explicitly authorized by the user.

## Delivery Workflow

Every feature MUST have a GitHub issue in the project board, a written specification, an
implementation plan, and dependency-ordered tasks. Security-sensitive routing, credentials, and
billing changes require a complete review artifact with zero unaddressed findings. Production
deployment MUST use the exact reviewed commit or verified artifact and record rollback paths.

## Governance

This constitution governs specifications, plans, implementation, review, and deployment for the
relay operations code. Amendments require a documented rationale, semantic version update, and
template consistency review. Pull requests MUST state how each applicable principle is satisfied;
unjustified violations block merge and production deployment. `AGENTS.md` remains the authority
for repository-specific language, database, and protected-project conventions.

**Version**: 1.0.0 | **Ratified**: 2026-08-10 | **Last Amended**: 2026-08-10
