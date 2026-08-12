# Feature Specification: Least-Privilege Channel Administration

**Issue**: https://github.com/qiaoguan1/new-api/issues/24
**Branch**: `codex/issue-24-admin-least-privilege`
**Status**: In Progress

## Requirements

- The service runs as a dedicated non-login system user.
- Direct requests without the trusted reverse-proxy token receive HTTP 403.
- The reverse proxy replaces any client-supplied token with a private value.
- The process can write only channel configuration and generated data paths.
- The process receives no Linux capabilities, Docker socket, or docker group.
- Credential save, balance fetch, monitor regeneration and read APIs keep working.
- Credentials, token material and internal ledgers are not committed or logged.
