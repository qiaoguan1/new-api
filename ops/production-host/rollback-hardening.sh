#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 || "$#" -ne 1 ]]; then
  echo 'usage: sudo rollback-hardening.sh /absolute/backup/directory' >&2
  exit 1
fi

backup_dir="$1"
case "$backup_dir" in
  /opt/ai-api-stack/backups/issue23-host-hardening-*) ;;
  *) echo 'refusing unexpected backup directory' >&2; exit 1 ;;
esac

test -f "$backup_dir/sshd_config"
test -d "$backup_dir/sshd_config.d"

mv /etc/ssh/sshd_config.d "/etc/ssh/sshd_config.d.issue23-rollback-$(date +%Y%m%d-%H%M%S)"
cp -a "$backup_dir/sshd_config.d" /etc/ssh/sshd_config.d
cp -a "$backup_dir/sshd_config" /etc/ssh/sshd_config
sshd -t
systemctl reload ssh

if [[ -f "$backup_dir/admin-server.py.before-bind" && -f "$backup_dir/nginx-default.before-bind.conf" ]]; then
  install -m 755 "$backup_dir/admin-server.py.before-bind" \
    /opt/ai-api-stack/channel-monitor/scripts/admin-server.py
  install -m 644 "$backup_dir/nginx-default.before-bind.conf" \
    /opt/ai-api-stack/nginx/conf.d/default.conf
  rm -f /etc/systemd/system/channel-monitor-admin.service.d/20-network-hardening.conf
  systemctl daemon-reload
  systemctl restart channel-monitor-admin.service
  (cd /opt/ai-api-stack && docker compose up -d --no-deps --force-recreate nginx)
fi

rm -f /etc/fail2ban/jail.d/sshd.local
systemctl restart fail2ban || true

if [[ "${DISABLE_UFW_ON_ROLLBACK:-0}" == "1" ]]; then
  ufw --force disable
else
  echo 'UFW remains active to avoid re-exposing TCP 8791.'
fi

echo 'Host hardening configuration rolled back.'
