# Implementation Plan: Paisio Settlement, Isolated Pricing, and Daily Operations Digest

**Branch**: `codex/issue-67-paisio-pricing-digest` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

## Summary

Replace Paisio's incorrect task-count cost interpretation with a two-source exact collector, remove the obsolete global credential gate from automatic pricing while retaining model-scoped guards, and add a private daily digest builder plus a root-authenticated structured email endpoint.

## Technical Context

**Languages**: Python 3.12, Go 1.22  
**Storage**: Existing JSON ledgers/pricing history, gateway SQLite, private 0600 digest state  
**Transport**: Existing root-authenticated NewAPI internal notification path and configured SMTP  
**Schedule**: Asia/Shanghai; digest retries during a bounded post-pricing window  
**Constraints**: No paid probe, no catalog-price settlement, no secret output, no video changes through generic pricing

## Implementation Sequence

1. Add failing collector fixtures for task-count quota plus request-scoped billing rows.
2. Implement Paisio exact ledger collection in the gateway and daily reconciliation pipeline.
3. Add a failing automatic-pricing integration test for unrelated incomplete credentials and remove only the global abort.
4. Add digest builder, idempotent delivery state, structured NewAPI email endpoint, and tests.
5. Run full suites and security review.
6. Back up production sources, options, gateway SQLite and container configuration.
7. Deploy dark; verify live Paisio evidence, then approve Paisio and test deterministic routing without a paid generation.
8. Install the Beijing-time digest schedule, send one explicit test digest, and run ten read-only rounds.

