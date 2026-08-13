# Implementation Plan: Video Reference Contract v2.2

1. Restore the frozen v2.1 request contract and add rejection coverage for all reference-video/audio aliases.
2. Add v2.2 as a separately persisted contract version with three default-off feature switches.
3. Publish unavailable provider-neutral capability and pricing profiles until reviewed evidence exists.
4. Add a pure v2.2 metadata validator and URL-free stable identity helper without connecting paid submission.
5. Preserve existing billing, settlement, Webhook, and delivery behavior; do not guess upstream adapter fields.
6. Run the full suite, security/comprehensive review, CI, merge, and deploy only the default-disabled boundary.

No database migration is required. Existing tasks retain their stored contract version. The new paid submission path intentionally remains unreachable.
