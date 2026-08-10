# Internal Contract: Route Decision

The route decision is internal operational metadata. It is never returned in ordinary downstream
job snapshots or public capability responses.

## Persisted shape

```json
{
  "catalog_revision": "revision-id",
  "selection_reason": "balanced",
  "route_index": 0,
  "route_plan": [
    {
      "provider_id": "internal-provider",
      "upstream_model": "provider-model",
      "adapter_revision": "adapter-revision",
      "resolution": "720p",
      "send_resolution": true
    }
  ],
  "route_history": []
}
```

## Safe fallback event

```json
{
  "at": 0,
  "from_index": 0,
  "to_index": 1,
  "failure_code": "bounded-code",
  "reason": "definitive_pre_creation_failure"
}
```

## Invariants

- The plan contains no credential, header, token, prompt, media URL, or provider response body.
- The plan is immutable after job creation.
- Advancing is atomic and only moves to the immediately following candidate.
- An uncertain failure or a non-empty upstream task identifier can never advance the route.
- Public snapshots omit provider, route plan, selection score, weights, and history.
