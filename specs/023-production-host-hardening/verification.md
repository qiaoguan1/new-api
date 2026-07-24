# Verification

**Pull request**: [#25](https://github.com/qiaoguan1/new-api/pull/25)

## Production evidence

- Rollback directory:
  `/opt/ai-api-stack/backups/issue23-host-hardening-20260724-170900`
- Rollback files and before/after evidence are mode-restricted and verified by
  `SHA256SUMS`.
- A new root public-key session succeeded before and after SSH reload and UFW
  activation.
- Effective SSH policy: password authentication disabled, root restricted to
  public-key authentication, and public-key authentication enabled.
- UFW is active with public TCP 22/80/443 only; TCP 8791 is allowed only from
  `172.18.0.0/16`.
- Fail2ban is active with the `sshd` jail and immediately banned active brute
  force sources.
- The administration API listens only on `172.18.0.1:8791`; it no longer binds
  the public interface.
- Nginx reaches the administration API through `172.18.0.1`, while the public
  HTTPS administration route requires Basic Auth and returns HTTP 401 without
  credentials.
- Direct external TCP 8791 requests time out.
- NewAPI returns HTTP 200 and no production container is unhealthy.

## Tests

- Initial host verification: failed on seven expected insecure controls.
- Python patcher tests: 4 passed.
- Python bytecode compilation: passed.
- Bash syntax validation: passed for all deployment and rollback scripts.
- Active-policy rerun guard: passed without creating another backup or resetting
  UFW.
- Final production validation: 10/10 rounds passed SSH, UFW, Fail2ban, listener,
  Nginx bridge reachability, patch integrity, NewAPI health, container health,
  Basic Auth boundary, and public 8791 denial.

## Notable implementation corrections

1. Added the Debian `python3-systemd` dependency after the initial Fail2ban
   systemd backend start failed.
2. Added a Fail2ban socket-readiness wait to prevent a restart race.
3. Added an active-UFW guard so rerunning the staging script cannot reset a live
   firewall.
4. Added a secure-by-default loopback bind and explicit production Docker bridge
   bind so protection does not depend on UFW alone.
