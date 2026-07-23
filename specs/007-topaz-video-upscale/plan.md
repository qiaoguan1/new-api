# Implementation Plan: Topaz Video Upscaling Channel

**Branch**: `codex/issue-7-topaz-video-upscale` | **Date**: 2026-07-23 | **Spec**: `spec.md`

## Technical Approach

1. Allocate a channel type and expose it through backend/frontend channel metadata.
2. Add a Topaz task package containing the documented upscaling model family, live status-based
   discovery, request/response DTOs, and the asynchronous task adapter.
3. Reuse the existing `/v1/videos` task lifecycle. Parse multipart input, create an express job,
   upload the source to the single presigned HTTPS URL, poll status, and feed the signed result URL
   into the existing content proxy.
4. Route both saved-channel and unsaved-channel model fetch operations through Topaz discovery.
5. Make channel testing call only the free status endpoint.
6. Verify locally, conduct comprehensive and security reviews, then back up the production source,
   image, database channel rows, and abilities before deployment.
7. Deploy the reviewed image, create the channel transactionally, refresh models read-only, and
   verify health and public model metadata without submitting video work.

## Security and Reliability Decisions

- The API key remains in the production channel table and is never embedded in source or fixtures.
- Presigned upload URLs are accepted only from the Topaz create response, must be HTTPS, are never
  logged, and receive no Topaz authentication header. Upload clients have a bounded timeout and do
  not follow redirects.
- Multipart request bodies are capped before parsing, and upstream JSON responses are size-bounded.
- The adapter rejects ambiguous multi-part upload responses instead of uploading an incomplete file.
- No automatic channel test or deployment verification may create a billable Topaz request.
- Stream options are unsupported and are not added to `streamSupportedChannels`.

## Verification

- `go test ./relay/channel/task/topaz ./controller ./common`
- `go test ./...`
- `bun run typecheck` and `bun run build` from `web/default`
- targeted ESLint for the changed channel configuration files
- `gofmt -d` and `git diff --check`
- repository/change-set credential scan
- production read-only `/video/status`, database row, abilities, `/api/status`, and container health

## Verification Result

- Focused and full Go test suites passed in the isolated Linux build workspace.
- Changed frontend files passed ESLint; TypeScript typecheck and production build passed.
- Security review fixed request-body bounds, HTTP timeouts/redirects, JSON bounds, and presigned URL redaction.
- Production image `new-api-fixed:topaz-video-20260723` is healthy.
- Channel ID 43 has 31 live upscaling models and 31 enabled abilities in group `视频`.
- Read-only Topaz status verification reported available; no paid task was created.
