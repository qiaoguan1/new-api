# Implementation Plan: Upstream Credential and Cost Coverage Repair

**Branch**: `codex/issue-5-upstream-cost-coverage`  
**Spec**: `specs/005-upstream-cost-coverage/spec.md`

## Technical Approach

1. Parse the operator file locally without displaying secrets; upload it to a root-only temporary
   path and atomically merge normalized entries into the production credential JSON.
2. Version the production fetch/audit scripts under `ops/channel-monitor/scripts/`, excluding all
   credential and runtime JSON files.
3. Add pure helpers for configured-model parsing, mapping normalization, probe-model selection, and
   pricing-catalog intersection, then cover them with unit tests.
4. Persist `configured_models` separately from `models` price details in the daily audit. Make the
   pricing worker discover from `configured_models`, while keeping backward compatibility with old
   audit snapshots.
5. Deploy scripts only after focused tests, syntax checks, diff review, and secret scanning.
6. Back up production files, run fetch/audit/pricing in schedule order, then manually compare the
   credential coverage, audit inventory, cost decisions, database options, and public API prices.

## Safety Decisions

- No passwords or raw bill rows in captured output.
- No automatic call to every discovered model.
- No price mutation when actual-cost evidence or channel health is absent.
- No channel model-list mutation based only on name heuristics.
- Every credential/script/pricing modification has a timestamped rollback artifact.

