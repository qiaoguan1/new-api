# Internal Contract: Paisio-First Route Decision

```json
{
  "selection_reason": "fixed_provider_priority_v1",
  "route_index": 0,
  "route_plan": [
    {"provider_id": "paisio", "priority": 10},
    {"provider_id": "toonflow", "priority": 20}
  ],
  "route_history": []
}
```

The actual persisted candidate omits `priority` because order is authoritative and immutable.
Credentials, prompts, media URLs, provider response bodies, and routing metadata remain absent from
public responses.

Fallback is permitted only when the current submission definitively failed before task creation.
Single-candidate plans use `capability_only_v1` instead of the shared-route reason.
