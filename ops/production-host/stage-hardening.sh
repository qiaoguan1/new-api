#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo 'stage-hardening.sh must run as root' >&2
  exit 1
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active$'; then
  echo 'refusing to stage over an active UFW policy' >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stack_dir="/opt/ai-api-stack"
backup_dir="$stack_dir/backups/issue23-host-hardening-$(date +%Y%m%d-%H%M%S)"
app_net_subnet="172.18.0.0/16"

install -d -m 700 "$backup_dir"
cp -a /etc/ssh/sshd_config "$backup_dir/sshd_config"
cp -a /etc/ssh/sshd_config.d "$backup_dir/sshd_config.d"
cp -a /root/.ssh/authorized_keys "$backup_dir/root-authorized_keys"
systemctl cat channel-monitor-admin.service > "$backup_dir/channel-monitor-admin.service"
cp -a "$stack_dir/nginx/conf.d/default.conf" "$backup_dir/nginx-default.conf"
sshd -T > "$backup_dir/sshd-effective.before.txt"
iptables-save > "$backup_dir/iptables.before.rules"
dpkg-query -W ufw fail2ban > "$backup_dir/packages.before.txt" 2>&1 || true

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ufw fail2ban python3-systemd

install -m 644 "$script_dir/sshd-hardening.conf" /etc/ssh/sshd_config.d/99-ai-api-hardening.conf
sshd -t
systemctl reload ssh

install -m 644 "$script_dir/fail2ban-sshd.local" /etc/fail2ban/jail.d/sshd.local
fail2ban-client -t
systemctl enable --now fail2ban
systemctl restart fail2ban
for _ in {1..20}; do
  if fail2ban-client ping >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
fail2ban-client ping >/dev/null

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw logging low
ufw allow 22/tcp comment 'SSH key access'
ufw allow 80/tcp comment 'HTTP redirect'
ufw allow 443/tcp comment 'HTTPS services'
ufw allow from "$app_net_subnet" to any port 8791 proto tcp comment 'Nginx to channel admin'

sshd -T > "$backup_dir/sshd-effective.staged.txt"
ufw status verbose > "$backup_dir/ufw-staged.txt"
fail2ban-client status sshd > "$backup_dir/fail2ban-sshd.staged.txt"
(
  cd "$backup_dir"
  sha256sum sshd_config root-authorized_keys channel-monitor-admin.service nginx-default.conf \
    sshd-effective.before.txt iptables.before.rules packages.before.txt \
    sshd-effective.staged.txt ufw-staged.txt fail2ban-sshd.staged.txt > SHA256SUMS
)
chmod -R go-rwx "$backup_dir"

printf 'STAGED_BACKUP_DIR=%s\n' "$backup_dir"
printf 'UFW remains inactive until activate-hardening.sh is run.\n'
