#!/bin/sh
set -eu

stack=/opt/ai-api-stack
release="$stack/releases/issue137-prod-eb65d7032"
candidate=issue137-new-api-candidate
current=$(cd "$stack" && docker compose ps -q new-api)

if docker ps -a --format '{{.Names}}' | grep -Fxq "$candidate"; then
  echo 'candidate name already exists' >&2
  exit 1
fi

install -d -m 700 "$release/candidate-data" "$release/candidate-logs"
envfile=$(mktemp "$release/runtime-env.XXXXXX")
chmod 600 "$envfile"
docker inspect "$current" --format '{{range .Config.Env}}{{println .}}{{end}}' > "$envfile"

cleanup() {
  rm -f "$envfile"
  docker rm -f "$candidate" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

id=$(docker run -d \
  --name "$candidate" \
  --network ai-api-stack_stack-internal \
  -p 127.0.0.1:13001:3000 \
  --env-file "$envfile" \
  -v "$release/candidate-data:/data" \
  -v "$release/candidate-logs:/app/logs" \
  -v "$stack/channel-monitor/data:/channel-monitor-data:ro" \
  -v "$stack/secrets/wechatpay/apiclient_key.pem:/run/secrets/wechatpay/apiclient_key.pem:ro" \
  -v "$stack/secrets/wechatpay/pub_key.pem:/run/secrets/wechatpay/pub_key.pem:ro" \
  new-api-fixed:issue137-eb65d7032 \
  --log-dir /app/logs)
rm -f "$envfile"

attempt=1
while [ "$attempt" -le 40 ]; do
  if curl -fsS --max-time 3 http://127.0.0.1:13001/api/status > "$release/candidate-status.json"; then
    break
  fi
  sleep 1
  attempt=$((attempt + 1))
done
curl -fsS --max-time 3 http://127.0.0.1:13001/api/status > "$release/candidate-status.json"

python3 - <<'PY'
import json

path = "/opt/ai-api-stack/releases/issue137-prod-eb65d7032/candidate-status.json"
with open(path, encoding="utf-8") as handle:
    status = json.load(handle)
assert status.get("success") is True
print("candidate_status=ok")
print("system_name=" + str(status.get("data", {}).get("system_name", "")))
print("task_enabled=" + str(bool(status.get("data", {}).get("enable_task"))).lower())
PY

docker inspect "$candidate" \
  --format 'candidate_running={{.State.Running}} image_id={{.Image}} started={{.State.StartedAt}}'
printf 'candidate_logs_tail\n'
docker logs --tail 40 "$candidate" 2>&1 |
  sed -E 's/(password|secret|token|authorization|key)([=: ]+)[^ ,]+/\1\2[REDACTED]/Ig'
printf 'candidate_id=%s\n' "$id"
