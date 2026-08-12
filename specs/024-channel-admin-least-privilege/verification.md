# Production Verification

- Rollback: `/opt/ai-api-stack/backups/issue24-admin-hardening-final-20260813-012519`.
- Network service user: `channel-monitor-admin`; no Docker supplementary group.
- Direct bridge request without the trusted header: HTTP 403.
- Trusted direct GET and unchanged configuration POST: HTTP 200.
- Public administrator route without Basic Auth: HTTP 401.
- Regeneration request is handled by an isolated, non-networked oneshot; only the
  oneshot has the Docker supplementary group.
- Credential prefix preview was removed; only fixed `****` is returned.
- Internal ledger remains mode 0600; sanitized monitor JSON remains mode 0644.
- Nginx and API remained healthy with zero restarts in ten verification rounds.
