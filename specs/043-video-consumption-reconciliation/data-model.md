# Data Model: Video Consumption Reconciliation
Public model snapshot contains only stable model, resolution, availability, request count, success
count, failed count and success rate.
## Provider Usage Record

- `provider_id` (private)
- `provider_task_id`
- `raw_model` and normalized `stable_model`
- `state`: completed, failed, running, refunded, unknown
- `created_at`, `completed_at` in ISO 8601
- `actual_cost_cny`: nullable
- `evidence_source`, `fetched_at`

Only completed authenticated records may have a positive actual cost. The identity key is
`provider_id + provider_task_id`; records without an identifier cannot be exact-matched.

## Relay Job Record

- `relay_job_id`, `provider_id`, `upstream_task_id`
- `stable_model`, `resolution`, `status`
- `created_at`, `updated_at`
- `relay_sale_cny`: nullable and independent of provider cost

## Reconciliation Row

- `match_status`: exact, inferred_unique, unmatched, ambiguous
- `relay_job_id`, `provider_task_id`
- `stable_model`, `resolution`, `status`
- `relay_sale_cny`, `upstream_actual_cost_cny`
- `actual_cost_status`: actual, zero_verified, unknown
- `evidence_source`, `fetched_at`

## Monitor Snapshots

Private provider snapshot includes counts, success rate, actual-cost coverage and last fetch time.
Public model snapshot contains only stable model, resolution, availability, request count, success
count, failed count and success rate.
