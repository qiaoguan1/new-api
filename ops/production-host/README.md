# Production host hardening

These scripts stage and activate Issue #23 host controls. Run `stage-hardening.sh`
first. Establish a new SSH key session from a separate client before running
`activate-hardening.sh`.

The administration server defaults to loopback and production explicitly binds
it to the host-side `app-net` gateway (`172.18.0.1`). Nginx uses that bridge
address instead of the public server address. The UFW policy provides a second
boundary: it exposes only TCP 22, 80, and 443 publicly and permits TCP 8791 only
from the production `app-net` subnet (`172.18.0.0/16`).

`patch_admin_network.py` applies the fail-closed source and Nginx transformation.
Install `channel-monitor-admin-network.conf` as
`/etc/systemd/system/channel-monitor-admin.service.d/20-network-hardening.conf`,
then restart the administration service and recreate Nginx so its bind mount
observes the atomically replaced configuration file.

Use `verify-hardening.sh` after activation. Public denial of port 8791 must also
be tested from a machine outside the server. `rollback-hardening.sh` requires the
exact backup directory created by the staging script and intentionally keeps UFW
active. Set `DISABLE_UFW_ON_ROLLBACK=1` only for an explicit full firewall
rollback after confirming another control protects TCP 8791.
