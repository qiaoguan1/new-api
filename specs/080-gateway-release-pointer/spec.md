# Feature Specification: Reproducible Running Gateway Release

**Issue**: https://github.com/qiaoguan1/new-api/issues/80
**Branch**: `codex/issue-80-gateway-release-pointer`
**Status**: In Progress

## Requirements

- Snapshot source from the exact immutable image used by the running production
  container; do not infer it from an older release directory.
- Record image tag/digest, command, working directory, ports, networks, restart
  policy, bind mounts and environment variable names without secret values.
- Keep state and secret mounts external; do not copy SQLite or credentials.
- Store the release root-owned mode 0700 with file hashes and an offline image
  archive checksum.
- Atomically update `services-video-job-gateway-current` only after every hash and
  production invariant verifies.
- Do not restart the running container or create paid tasks.
