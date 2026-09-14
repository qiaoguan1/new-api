# Banana Chat-Image Adapter Specification

**Issue**: #160
**Status**: In Progress

## Goal

Restore `banana-flash` and `banana-pro` without changing the existing image-job gateway by
translating OpenAI image-generation requests into the chat-multimodal contract supported by the
validated upstreams.

## Requirements

1. Expose authenticated `POST /v1/images/generations` and read-only health/model endpoints.
2. Map `banana-flash` to Haina vip first and Rolldek second.
3. Map `banana-pro` to Rolldek only.
4. Return an OpenAI-compatible image response containing upstream image references.
5. Permit only one image, bounded prompts, bounded dimensions, and URL responses.
6. Fall back only after an explicit fast provider rejection proving generation did not start.
7. Never retry timeouts, transport uncertainty, empty successful responses, or other ambiguous
   outcomes.
8. Never log prompts, keys, returned image URLs, image bodies, or upstream response bodies.
9. Run as an isolated non-root container with read-only secret mounts and no host port.
10. Keep the original Paisio Banana channel disabled until the adapter passes a dark run.

## Success Criteria

- Unit tests cover the request, response, routing, fallback, and masking contracts.
- Haina Flash and Rolldek Pro each pass one bounded adapter-level generation.
- A new production channel exposes both stable model names through the adapter.
- Existing image gateway identity, environment, and container remain unchanged.
- Rollback removes the new channel, Nginx location, and adapter container without touching data.
