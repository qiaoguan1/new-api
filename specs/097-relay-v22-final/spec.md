# Specification: Relay-owned reference media v2.2

## Goal

The XingTu relay finishes and owns the complete `xtai-video-billing-v2.2`
contract before downstream implementation.  The downstream integrates once
against the final provider-neutral contract and never handles upstream names,
credentials, billing modes, or recovery.

## Requirements

1. Reference video and reference audio are accepted only under v2.2.  v2.1
   remains fail-closed for every legacy and canonical reference field.
2. MP4 reference video and MP3/WAV/AAC/M4A reference audio use ordered, URL-free media
   identities.  Audio reference requires at least one image or reference video.
3. `generate_audio` controls output audio independently from reference audio.
4. The relay selects only routes that support the exact input combination.  It
   must never drop media, rewrite audio into a prompt, or silently downgrade.
5. Reservation uses the Ark official rate for the actual input mode multiplied
   by 1.5.  Any reference video selects the Ark with-video-input rate; image plus
   audio without video selects the no-video-input rate.
6. Final settlement remains trusted per-task upstream net cost multiplied by
   1.5, with the existing refund/supplement and webhook behavior.
7. Capabilities distinguish official support from currently executable route
   availability and publish the exact available combinations.
8. Signed media URLs are never persisted in webhook payloads or stable
   fingerprints. They are retained only inside the relay's private task record
   for bounded recovery. A route remains unavailable until its exact request mapping,
   task identity, and settlement collector are verified.
9. Existing v2.1 jobs and APIs remain compatible.

## Non-goals

- The downstream does not approve or configure upstream routes.
- The downstream does not calculate relay costs or query upstream billing.
- No paid upstream smoke test is performed without explicit authorization.

## Acceptance

- Deterministic tests cover validation, URL-free identity, input-mode pricing,
  route isolation, adapter payloads, task persistence, query, settlement, and
  webhook snapshots.
- Full gateway tests pass in the production image.
- Staging then production deploy with rollback and ten no-charge verification
  rounds.
- A single final downstream implementation document is delivered.
