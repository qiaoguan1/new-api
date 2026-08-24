#!/bin/sh
set -eu

stack=/opt/ai-api-stack
backup=/opt/ai-api-stack/backups/issue137-production-deploy-20260825-003507
release=/opt/ai-api-stack/releases/issue137-prod-eb65d7032
old_image=new-api-fixed:patrol-repair-4cca558c
old_image_id=sha256:7b1075dfbba08375e34002696c7b404bd56f141bca9a66c9188ef650333e3652
new_image=new-api-fixed:issue137-eb65d7032
new_image_id=sha256:63a028e3d11e974169db3c6b34e1e09d6209ac3b2e54628e9e04ac50aaa0c11d
activated=0

wait_healthy() {
  service_id=$(cd "$stack" && docker compose ps -q new-api)
  attempt=1
  while [ "$attempt" -le 120 ]; do
    status=$(docker inspect "$service_id" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
    [ "$status" = healthy ] && return 0
    [ "$status" = unhealthy ] && return 1
    sleep 1
    attempt=$((attempt + 1))
  done
  return 1
}

rollback() {
  trap - EXIT INT TERM
  cp -a "$backup/docker-compose.yml" "$stack/docker-compose.yml"
  cp -a "$backup/docker-compose.override.yml" "$stack/docker-compose.override.yml"
  cd "$stack"
  docker compose config --quiet
  docker compose up -d --no-deps --force-recreate new-api
  wait_healthy
  current=$(docker compose ps -q new-api)
  restored=$(docker inspect "$current" --format '{{.Image}}')
  [ "$restored" = "$old_image_id" ]
  curl -fsS --max-time 10 http://127.0.0.1:3000/api/status >/dev/null
  echo "rollback=complete image=$restored" >&2
}

on_exit() {
  code=$?
  if [ "$code" -ne 0 ] && [ "$activated" -eq 1 ]; then
    rollback || true
  fi
  exit "$code"
}
trap on_exit EXIT INT TERM

cd "$stack"
docker compose config --quiet
current=$(docker compose ps -q new-api)
[ "$(docker inspect "$current" --format '{{.Config.Image}}')" = "$old_image" ]
[ "$(docker inspect "$current" --format '{{.Image}}')" = "$old_image_id" ]
[ "$(docker image inspect "$new_image" --format '{{.Id}}')" = "$new_image_id" ]

docker ps --format '{{.Names}}|{{.ID}}' | grep -v '^ai-api-stack-new-api-1|' | sort > "$backup/non-newapi-containers.before"
cp -a "$stack/docker-compose.yml" "$backup/docker-compose.pre-activation.yml"

python3 - <<'PY'
from pathlib import Path

path = Path("/opt/ai-api-stack/docker-compose.yml")
old = "    image: new-api-fixed:patrol-repair-4cca558c\n"
new = "    image: new-api-fixed:issue137-eb65d7032\n"
text = path.read_text(encoding="utf-8")
if text.count(old) != 1:
    raise SystemExit("expected exactly one production image line")
temporary = path.with_suffix(".yml.issue137.tmp")
temporary.write_text(text.replace(old, new, 1), encoding="utf-8")
temporary.chmod(path.stat().st_mode)
temporary.replace(path)
PY

activated=1
docker compose config --quiet
docker compose up -d --no-deps --force-recreate new-api
wait_healthy

current=$(docker compose ps -q new-api)
[ "$(docker inspect "$current" --format '{{.Config.Image}}')" = "$new_image" ]
[ "$(docker inspect "$current" --format '{{.Image}}')" = "$new_image_id" ]

round=1
while [ "$round" -le 5 ]; do
  curl -fsS --max-time 10 http://127.0.0.1:3000/api/status >/dev/null
  curl -fsS --max-time 10 https://api.aixingtuyun.com/api/status >/dev/null
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 https://api.aixingtuyun.com/v1/models)
  [ "$code" = 401 ]
  ready=$(curl -fsS --max-time 10 https://sub.aixingtuyun.com/ready)
  READY="$ready" python3 - <<'PY'
import json
import os

ready = json.loads(os.environ["READY"])
assert ready.get("ok") is True
assert ready.get("accepting") is True
assert ready.get("draining") is False
assert ready.get("circuit", {}).get("open") is False
PY
  round=$((round + 1))
done

root=$(curl -fsS --max-time 10 https://api.aixingtuyun.com/)
printf '%s' "$root" | grep -F 'index.b420fa47ee.js' >/dev/null

docker ps --format '{{.Names}}|{{.ID}}' | grep -v '^ai-api-stack-new-api-1|' | sort > "$backup/non-newapi-containers.after"
cmp "$backup/non-newapi-containers.before" "$backup/non-newapi-containers.after"

docker inspect "$current" --format '{{json .State}}' > "$release/production-container-state.json"
docker image inspect "$new_image" > "$release/production-image-inspect.json"
curl -fsS --max-time 10 https://api.aixingtuyun.com/api/status > "$release/postdeploy-api-status.json"
curl -fsS --max-time 10 https://sub.aixingtuyun.com/ready > "$release/postdeploy-video-ready.json"
printf '%s\n' "$backup" > "$release/ROLLBACK_PATH"
printf '%s\n' "$old_image" > "$release/ROLLBACK_IMAGE"
ln -sfn "$release" "$stack/releases/new-api-current"
chmod 600 "$release"/production-container-state.json "$release"/production-image-inspect.json \
  "$release"/postdeploy-api-status.json "$release"/postdeploy-video-ready.json \
  "$release"/ROLLBACK_PATH "$release"/ROLLBACK_IMAGE

trap - EXIT INT TERM
echo "deployment=ok image=$new_image image_id=$new_image_id rounds=5 rollback=$backup"
