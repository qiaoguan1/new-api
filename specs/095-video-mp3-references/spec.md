# Feature Specification: Video Reference Contract v2.2

**Feature Branch**: `codex/issue-95-video-mp3-references`

**Created**: 2026-08-13
**Status**: Candidate freeze; production submission disabled

## Scope

Add a contract-isolated `xtai-video-billing-v2.2` boundary for reference video and reference audio. Existing v2.1 requests containing either reference type remain rejected before quote, reservation, task creation, or upstream submission.

## Requirements

- **FR-001**: v2.1 MUST reject `video`, `videos`, `audio`, `audios`, `reference_videos`, and `reference_audios`.
- **FR-002**: v2.2 MUST use only canonical `reference_videos` and `reference_audios` objects defined in the downstream freeze document.
- **FR-003**: The gateway MUST expose three independent, default-off switches: reference video, reference audio, and their combination.
- **FR-004**: Feature switches MUST NOT bypass exact route-capability and input-pricing gates.
- **FR-005**: Capabilities MUST distinguish official model support from route readiness: official support may be true while `available=false` until a route passes no-charge verification.
- **FR-006**: Candidate metadata validation MUST enforce HTTPS/443, non-private literal hosts, SHA-256, ordered roles, exact six-decimal duration, positive media properties, and platform count/size/duration ceilings.
- **FR-007**: Candidate audio accepts the official MP3/WAV pairs; candidate video accepts the currently verified official MP4 pair. Additional formats are capability-data changes, not downstream field changes.
- **FR-008**: Stable request identity MUST include ordered media metadata and SHA-256 but MUST exclude signed URLs and upstream registration IDs.
- **FR-009**: The gateway MUST NOT invent provider parameter names, limits, price multipliers, or registration flows. Adapter wiring remains blocked until written evidence and non-secret fixtures are reviewed.
- **FR-010**: No v2.2 paid task may be submitted by this issue.

## Acceptance Criteria

1. Existing v2.1 tests remain green and new tests prove every reference field fails closed.
2. v2.2 default-disabled submission creates zero jobs and zero reservations.
3. Rotating only signed URL query strings leaves the stable reference digest unchanged.
4. Invalid URL, MIME/codec, SHA, size, duration, video dimensions, audio sample rate/channels, count, or total duration is rejected deterministically.
5. `/v1/capabilities` publishes official support separately from route availability; `/v1/video-prices` publishes the existing Ark official base ×1.5 policy without an extra reference multiplier.
6. Full gateway suite and ten no-charge verification rounds pass.

## Deferred Activation Evidence

Activation requires the ten written confirmations in `docs/XTAI_VIDEO_REFERENCE_VIDEO_PROTOCOL_V22.md`, exact per-route capabilities, exact input pricing profiles, safe URL fetching and hashing, durable URL-free material registration state, settlement evidence, private TOS delivery, downstream staging Webhook, one separately approved paid smoke test, and staged rollout.
