# Plan

1. Extend the catalog with explicit reference-video, reference-audio, combined,
   and audio-count route capabilities.
2. Normalize v2.2 media into the existing durable job payload while preserving
   URL-free ordered identities and the v2.2 contract version.
3. Add Ark input-mode reservation pricing and reuse the v2.1 settlement path.
4. Add exact provider payload mappings only where the route shape is proven;
   keep every other route excluded from v2.2.
5. Publish exact capabilities/prices and media digests in query/webhooks.
6. Verify, review, merge, deploy, and produce the final downstream handoff.
