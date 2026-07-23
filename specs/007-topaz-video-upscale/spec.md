# Feature Specification: Topaz Video Upscaling Channel

**Feature Branch**: `codex/issue-7-topaz-video-upscale`
**Created**: 2026-07-23
**Status**: Complete
**Issue**: https://github.com/qiaoguan1/new-api/issues/7

## Goal

Add a native Topaz Labs channel that discovers currently available video upscaling models and
adapts the existing OpenAI-compatible asynchronous video API to Topaz's create, upload, status,
and download workflow.

## User Scenarios and Testing

### User Story 1 - Discover only available upscaling models (P1)

As an operator, I can fetch models for a Topaz channel and receive the intersection of Topaz's
live `supportedModels` response and the documented upscaling/enhancement model family.

**Acceptance scenarios**:

1. A successful `/video/status` response is authenticated with `X-API-Key` and returns only
   upscaling/enhancement models in upstream order.
2. Frame interpolation, colorization, stabilization, HDR, object-removal, and denoise-only models
   are excluded from this channel.
3. An unauthorized or malformed response fails without exposing the API key.

### User Story 2 - Upload a source video and create a Topaz job (P1)

As an API user, I can submit one MP4, MOV, or MKV source file through `/v1/videos`, select a Topaz
model and output settings, and receive the gateway's public task ID.

**Acceptance scenarios**:

1. The adapter rejects missing models, missing or multiple files, unsupported containers, invalid
   output sizes, invalid frame rates, and files larger than Topaz's 500 MB limit before creating a
   paid request.
2. The adapter creates `/video/express` with `X-API-Key`, uploads the source to the returned HTTPS
   URL, and never sends the Topaz key to object storage.
3. Model mapping is applied before the Topaz request, and the public response does not expose the
   upstream request ID.

### User Story 3 - Poll and download the enhanced video (P1)

As an API user, I can poll the public video task until it completes and retrieve its output through
the existing video content proxy.

**Acceptance scenarios**:

1. Topaz requested/accepted/processing/complete/canceled/failed states map to the gateway's queued,
   in-progress, completed, and failed states with progress preserved.
2. The signed download URL is stored only as task result data and is refreshed by status polling.
3. Errors are concise and do not include API keys, presigned upload URLs, or raw response bodies.

## Functional Requirements

- **FR-001**: Add a dedicated Topaz channel type with default base URL
  `https://api.topazlabs.com`.
- **FR-002**: Authenticate Topaz API calls with `X-API-Key`; never use bearer authentication.
- **FR-003**: Model discovery MUST call `/video/status` and filter the live response through a
  reviewed upscaling/enhancement allowlist derived from current Topaz model documentation.
- **FR-004**: The task adapter MUST use `/video/express` and exactly one HTTPS presigned upload URL.
- **FR-005**: The source MUST be one MP4, MOV, or MKV file no larger than 500 MB.
- **FR-006**: Output resolution and frame rate MUST be explicit and validated. Safe defaults MAY be
  used for audio transfer, output container, encoder, and compression.
- **FR-007**: Polling MUST use `/video/{requestId}/status` and preserve the signed result URL for the
  existing content proxy.
- **FR-008**: The channel test MUST use the free system-status/model-discovery call and MUST NOT
  create a paid video request.
- **FR-009**: Stream options are not applicable because Topaz video processing is asynchronous.
- **FR-010**: Production write-in MUST be backed up, atomic, and must not launch paid processing.

## Success Criteria

- Backend tests cover discovery filtering, authentication, validation, upload isolation, state
  mapping, and OpenAI-compatible task conversion.
- Relevant Go tests and frontend typecheck/build pass.
- Production channel `tp视频放大` is enabled, has the correct type/base URL/group, stores the key
  only in the database, and contains the live supported upscaling model list.
- A read-only production model refresh succeeds without paid usage.
