# Research: Paisio-First Video Routing

## Decision

Use the existing lower-number-first catalog priority tiers. Assign Paisio priority 10 and Toonflow
priority 20 for shared capabilities. Do not introduce a second routing configuration surface.

## Evidence

- `build_route_plan` already orders priority tiers before applying rendezvous ordering inside a tier.
- The checked-in catalog currently assigns both providers priority 10, which causes balanced first choice.
- The gateway persists the full plan before submission.
- `advance_route` already permits only atomic movement to the next candidate without a task ID.
- Adapter errors distinguish definite rejection from uncertain submission.

## Alternatives Rejected

- Hard-code provider names in `routing.py`: duplicates catalog policy and is harder to extend.
- Change enabled-provider environment order: the value is a set and cannot provide durable precedence.
- Fall back on every exception: risks duplicate generation after an upstream task was created.
