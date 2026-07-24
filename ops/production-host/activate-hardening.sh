#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo 'activate-hardening.sh must run as root' >&2
  exit 1
fi

sshd -t
[[ "$(sshd -T | awk '$1 == "passwordauthentication" {print $2}')" == "no" ]]
root_login="$(sshd -T | awk '$1 == "permitrootlogin" {print $2}')"
[[ "$root_login" == "without-password" || "$root_login" == "prohibit-password" ]]
[[ "$(systemctl is-active fail2ban)" == "active" ]]
fail2ban-client status sshd >/dev/null

ufw --force enable
ufw status verbose
