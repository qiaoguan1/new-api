# Data Model: Paisio-First Video Routing

No schema change is required.

## Route Candidate

- `provider_id`
- `upstream_model`
- `adapter_revision`
- `priority`: Paisio 10, Toonflow 20 for shared routes

## Route Decision

- `route_plan`: immutable ordered candidates
- `route_index`: active candidate, initially zero
- `selection_reason`: `fixed_provider_priority_v1` for shared routes or `capability_only_v1` for a
  single compatible route
- `route_history`: safe fallback events

## Invariants

- Existing jobs keep their saved plan even after catalog changes.
- Route index advances only to the next saved candidate.
- An uncertain outcome or a non-empty upstream task ID cannot advance.
