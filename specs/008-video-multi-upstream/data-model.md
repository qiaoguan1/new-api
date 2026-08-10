# Data Model: Multi-Provider Video Routing

## Route Candidate

- `provider_id`: reviewed internal provider identifier
- `upstream_model`: exact authenticated upstream model name
- `adapter_revision`: adapter contract revision
- `resolution`: required upstream resolution
- `send_resolution`: whether the adapter sends resolution explicitly
- `selection_score`: deterministic ordering score derived from the request identity

Validation rules:

- Provider must be configured and present in the reviewed catalog.
- Model, resolution, and route-specific constraints must match the normalized request.
- Credentials and base URLs are never embedded in the candidate.

## Route Decision

- `route_plan`: ordered immutable list of eligible Route Candidates
- `route_index`: zero-based active candidate
- `selection_reason`: `balanced`, `capability_only`, or `fallback_after_definitive_rejection`
- `route_history`: timestamped prior candidate and safe failure code
- `catalog_revision`: catalog used to create the plan

State rules:

- Created atomically with the queued job before any provider call.
- `route_index` can increase only while status is `submitting`, no upstream task identifier exists,
  and the failure is explicitly non-uncertain.
- The plan never changes after creation, even if the live catalog changes.

## Provider Health

- `provider_id`
- `definitive_failure_count`
- `window_started_at`
- `cooldown_until`
- `last_success_at`

State rules:

- Only definite pre-creation failures increment the failure sequence.
- Accepted, running, or successful submissions reset the sequence.
- Health affects new route plans only.

## Video Job Additions

Existing gateway jobs gain additive fields:

- `route_plan_json` (default `[]`)
- `route_index` (default `0`)
- `selection_reason` (default empty)
- `route_history_json` (default `[]`)

Legacy rows are valid: their stored provider is converted to a one-candidate effective plan when
read or recovered, without changing the assigned provider.

## State Transitions

```text
queued -> submitting -> running -> succeeded|failed|pending_review
                   \
                    -> queued (only safe fallback to next persisted candidate)
submitting -> uncertain (ambiguous outcome; never fallback)
```
