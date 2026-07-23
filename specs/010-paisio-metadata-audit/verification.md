# Verification

## Pre-deployment

- 46 Python unit tests pass.
- Python compilation and `git diff --check` pass.
- The patch applies to the current production script, passes `--check`, and a
  second application leaves the SHA-256 hash unchanged.
- A mocked execution of the patched production copy validates both root and
  `/v1` base URLs and records only `GET /v1/models`.
- AST inspection confirms `build_snapshot()` cannot call the legacy paid
  `probe_channel()` path.
- Pricing metadata persists only allowlisted model pricing fields and sanitized
  account model names; credentials and unknown fields are not persisted.

## Production

Pending backup, deployment, manual rerun, and post-deployment verification.
