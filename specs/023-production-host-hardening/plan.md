# Implementation Plan

1. Capture effective SSH, firewall, network, service, and endpoint state.
2. Back up SSH and host-security configuration on the server.
3. Install UFW and Fail2ban without enabling UFW yet.
4. Stage and validate the SSH drop-in and Fail2ban jail.
5. Verify a fresh public-key SSH connection, reload SSH, and verify again.
6. Patch the administration server to bind the Docker bridge, point Nginx to
   that bridge, and verify both sides before recreating Nginx.
7. Add UFW rules for 22/80/443 and Docker `app-net` access to 8791, then enable
   UFW and test the protected Nginx path before testing public denial.
8. Run application, container, firewall, SSH, and rollback verification.

Rollback restores the backed-up SSH configuration, disables UFW, removes the
Fail2ban jail, reloads SSH, and restarts Fail2ban if required.
