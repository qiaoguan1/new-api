# Feature Specification: Independent Daily Pricing Reliability

**Feature Branch**: `codex/issue-76-pricing-reliability`
**Created**: 2026-08-13
**Status**: In Progress
**Issue**: https://github.com/qiaoguan1/new-api/issues/76

## Goal

Keep generic actual-cost pricing isolated by affected channel/model and keep every
manually reviewed video alias on the approved Ark official price multiplied by
1.5, without allowing unknown video names to enter pricing.

## Requirements

- **FR-001**: Incomplete credentials MUST be reported but MUST NOT stop pricing
  models whose eligible sources have complete evidence.
- **FR-002**: A missing or incomplete source MUST block every model that can route
  through that source.
- **FR-003**: An enabled route matched by an approved exact source or global rule
  MUST remain on official video pricing even when a volatile discovery report
  temporarily omits it.
- **FR-004**: Parser, heuristic, unknown, conflicting, or identity-changing video
  mappings MUST fail closed.
- **FR-005**: Pricing writes MUST remain atomic and retain the existing movement,
  official-catalog, revision, and GroupRatio guards.

## Success Criteria

- Existing and new pricing tests pass.
- The production official-video dry-run includes all enabled reviewed aliases.
- The generic dry-run records incomplete unrelated credentials without a global
  failure after the next complete daily audit exists.
- Ten production read-only verification rounds pass without paid requests.
