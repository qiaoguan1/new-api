# Production Verification

- Previous pointer: `/opt/xtai/releases/video-p0-42021efa`.
- Current pointer: `/opt/xtai/releases/video-paisio-dual-id-09260a84`.
- Running and snapshotted image ID:
  `sha256:2dfa762c0e23413a6ad5caff37a7ff1bc9799ccf883bda0f628f4cc647f8bd60`.
- Eleven source/config files were extracted from immutable `/app`.
- The release contains a private offline image archive, source and release
  SHA-256 inventories, and a secret-free runtime manifest.
- State `/data` remains an external read/write bind and billing secrets remain
  an external read-only bind.
- The production container was not restarted or recreated.
- Ten verification rounds passed with zero restarts, zero pending settlements,
  zero Webhook backlog, and an exact pointer/image digest match.
