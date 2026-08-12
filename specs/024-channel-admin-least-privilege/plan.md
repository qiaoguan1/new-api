# Implementation Plan

1. Patch the production-only admin server with fail-closed proxy-token validation.
2. Add a canonical hardened systemd unit for a dedicated service user.
3. Install a generated secret into separate systemd and Nginx private files.
4. Limit ownership/write paths to configuration and generated data; queue the
   Docker-backed generator through an isolated, non-networked oneshot path unit.
5. Back up production code/config, deploy, and exercise direct/proxied paths.
6. Run security review and ten production validation rounds.
