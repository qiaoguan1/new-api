#!/usr/bin/env bash
set -euo pipefail

failures=0

check_equal() {
  local label="$1"
  local actual="$2"
  local expected="$3"
  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS %s: %s\n' "$label" "$actual"
  else
    printf 'FAIL %s: expected %s, got %s\n' "$label" "$expected" "$actual" >&2
    failures=$((failures + 1))
  fi
}

sshd_config="$(sshd -T)"
check_equal password_authentication "$(awk '$1 == "passwordauthentication" {print $2}' <<<"$sshd_config")" "no"
root_login="$(awk '$1 == "permitrootlogin" {print $2}' <<<"$sshd_config")"
if [[ "$root_login" == "without-password" || "$root_login" == "prohibit-password" ]]; then
  printf 'PASS root_login: %s\n' "$root_login"
else
  printf 'FAIL root_login: expected key-only root login, got %s\n' "$root_login" >&2
  failures=$((failures + 1))
fi
check_equal public_key_authentication "$(awk '$1 == "pubkeyauthentication" {print $2}' <<<"$sshd_config")" "yes"
check_equal ufw_state "$(ufw status | awk 'NR == 1 {print tolower($2)}')" "active"
check_equal fail2ban_service "$(systemctl is-active fail2ban)" "active"
check_equal fail2ban_sshd_jail "$(fail2ban-client status sshd >/dev/null 2>&1 && printf active || printf inactive)" "active"

if ss -H -lnt 'sport = :8791' | grep -Eq '172\.18\.0\.1:8791[[:space:]]'; then
  printf 'PASS admin listener: 172.18.0.1:8791\n'
else
  printf 'FAIL admin listener is not restricted to 172.18.0.1:8791\n' >&2
  failures=$((failures + 1))
fi

if grep -Fq 'proxy_pass http://172.18.0.1:8791/channel-monitor/admin/;' \
  /opt/ai-api-stack/nginx/conf.d/default.conf; then
  printf 'PASS Nginx admin upstream uses the Docker bridge\n'
else
  printf 'FAIL Nginx admin upstream does not use the Docker bridge\n' >&2
  failures=$((failures + 1))
fi

if docker exec ai-api-stack-nginx-1 wget -q -T 5 -O /dev/null \
  http://172.18.0.1:8791/channel-monitor/admin/config; then
  printf 'PASS Nginx can reach the restricted admin listener\n'
else
  printf 'FAIL Nginx cannot reach the restricted admin listener\n' >&2
  failures=$((failures + 1))
fi

nginx_worker_gid="$(docker exec ai-api-stack-nginx-1 id -g nginx)"
auth_metadata="$(docker exec ai-api-stack-nginx-1 \
  stat -c '%u:%g:%a' /etc/nginx/auth/channel-monitor.htpasswd)"
check_equal nginx_auth_permissions "$auth_metadata" "0:${nginx_worker_gid}:640"
if docker exec -u nginx ai-api-stack-nginx-1 \
  test -r /etc/nginx/auth/channel-monitor.htpasswd; then
  printf 'PASS Nginx worker can read the Basic Auth file\n'
else
  printf 'FAIL Nginx worker cannot read the Basic Auth file\n' >&2
  failures=$((failures + 1))
fi

if ufw status numbered | grep -Eq '8791/tcp[[:space:]]+ALLOW IN[[:space:]]+172\.18\.0\.0/16'; then
  printf 'PASS docker admin rule: 172.18.0.0/16 -> 8791/tcp\n'
else
  printf 'FAIL docker admin rule is absent\n' >&2
  failures=$((failures + 1))
fi

for public_port in 22 80 443; do
  if ufw status numbered | grep -Eq "${public_port}/tcp[[:space:]]+ALLOW IN[[:space:]]+Anywhere"; then
    printf 'PASS public service rule: %s/tcp\n' "$public_port"
  else
    printf 'FAIL public service rule is absent: %s/tcp\n' "$public_port" >&2
    failures=$((failures + 1))
  fi
done

if [[ "$failures" -ne 0 ]]; then
  printf 'FAILED checks=%d\n' "$failures" >&2
  exit 1
fi

printf 'ALL HOST HARDENING CHECKS PASSED\n'
