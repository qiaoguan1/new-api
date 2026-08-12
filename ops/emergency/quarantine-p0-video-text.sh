#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

GATEWAY="${GATEWAY:-xtai-video-job-gateway-v2-production}"
GATEWAY_STATE="${GATEWAY_STATE:-/opt/xtai/state/video-billing-v2-production}"
SECRET_DIR="${SECRET_DIR:-/opt/xtai/secrets/video-billing}"
STACK_DIR="${STACK_DIR:-/opt/ai-api-stack}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-ai-api-stack-postgres-1}"
NEWAPI_CONTAINER="${NEWAPI_CONTAINER:-ai-api-stack-new-api-1}"
TARGET_CHANNEL_ID="${TARGET_CHANNEL_ID:-39}"
TARGET_CHANNEL_NAME="${TARGET_CHANNEL_NAME:-Code Plan · 文字}"
APPROVED_PROVIDERS="${APPROVED_PROVIDERS:-toonflow}"

if [[ ! "$TARGET_CHANNEL_ID" =~ ^[0-9]+$ ]]; then
  echo "target channel id must be numeric" >&2
  exit 24
fi

DB_USER="$(docker exec "$POSTGRES_CONTAINER" sh -lc 'printf %s "$POSTGRES_USER"')"
DB_NAME="$(docker exec "$POSTGRES_CONTAINER" sh -lc 'printf %s "$POSTGRES_DB"')"

stamp="$(date +%Y%m%d-%H%M%S)"
image="$(docker inspect -f '{{.Config.Image}}' "$GATEWAY")"
backup_dir="$GATEWAY_STATE/backups/p0-$stamp"
mkdir -p "$backup_dir" "$STACK_DIR/backups"

docker inspect "$GATEWAY" > "$backup_dir/gateway-inspect.json"
cp -a "$GATEWAY_STATE/gateway.env" "$backup_dir/gateway.env.before" 2>/dev/null || true
docker exec "$GATEWAY" python -c \
  "import sqlite3; s=sqlite3.connect('/data/video-jobs.sqlite3'); d=sqlite3.connect('/data/backup-p0-$stamp.sqlite3'); s.backup(d); assert d.execute('pragma integrity_check').fetchone()[0]=='ok'; d.close(); s.close()"

python3 - "$GATEWAY" "$GATEWAY_STATE/gateway.env.next" "$APPROVED_PROVIDERS" <<'PY'
import json
import os
import subprocess
import sys

name, target, approved = sys.argv[1:]
config = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
environment = {}
for item in config["Config"].get("Env") or []:
    key, separator, value = item.partition("=")
    if separator:
        if "\n" in key or "\r" in key or "\n" in value or "\r" in value:
            raise RuntimeError("container environment cannot be represented safely as an env file")
        environment[key] = value
environment["VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS"] = approved

temporary = target + ".tmp"
with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
    for key in sorted(environment):
        stream.write(f"{key}={environment[key]}\n")
    stream.flush()
    os.fsync(stream.fileno())
os.chmod(temporary, 0o600)
os.replace(temporary, target)
PY

rollback_container="${GATEWAY}-rollback-p0-${stamp}"
docker rename "$GATEWAY" "$rollback_container"
docker stop -t 30 "$rollback_container" >/dev/null

restore_gateway() {
  docker rm -f "$GATEWAY" >/dev/null 2>&1 || true
  docker rename "$rollback_container" "$GATEWAY"
  docker start "$GATEWAY" >/dev/null
}

if ! docker run -d \
  --name "$GATEWAY" \
  --restart unless-stopped \
  --env-file "$GATEWAY_STATE/gateway.env.next" \
  -v "$GATEWAY_STATE/data:/data" \
  -v "$SECRET_DIR:/run/secrets/video-billing:ro" \
  --network app-net \
  --network-alias "$GATEWAY" \
  --network-alias video-job-gateway-v2-production \
  "$image" >/dev/null; then
  restore_gateway
  exit 20
fi

gateway_healthy=0
for _ in $(seq 1 30); do
  if docker exec "$GATEWAY" python -c \
    "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=2)); assert d.get('ok') is True" \
    >/dev/null 2>&1; then
    gateway_healthy=1
    break
  fi
  sleep 1
done
if [[ "$gateway_healthy" -ne 1 ]]; then
  restore_gateway
  exit 21
fi

mv "$GATEWAY_STATE/gateway.env" "$backup_dir/gateway.env.stale" 2>/dev/null || true
mv "$GATEWAY_STATE/gateway.env.next" "$GATEWAY_STATE/gateway.env"
chmod 600 "$GATEWAY_STATE/gateway.env"

database_backup="$STACK_DIR/backups/p0-codeplan-$stamp.dump"
docker exec "$POSTGRES_CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$database_backup"
chmod 600 "$database_backup"

actual_channel_name="$(docker exec "$POSTGRES_CONTAINER" psql -At \
  -U "$DB_USER" -d "$DB_NAME" \
  -c "SELECT name FROM channels WHERE id = $TARGET_CHANNEL_ID")"
if [[ "$actual_channel_name" != "$TARGET_CHANNEL_NAME" ]]; then
  echo "target channel identity mismatch" >&2
  exit 23
fi

docker exec -i "$POSTGRES_CONTAINER" psql \
  -v ON_ERROR_STOP=1 \
  -U "$DB_USER" -d "$DB_NAME" >/dev/null <<SQL
BEGIN;
UPDATE channels
SET status = 2
WHERE id = $TARGET_CHANNEL_ID
  AND status = 1;
UPDATE abilities
SET enabled = false
WHERE channel_id = $TARGET_CHANNEL_ID
  AND enabled = true;
COMMIT;
SQL

docker restart "$NEWAPI_CONTAINER" >/dev/null
newapi_healthy=0
for _ in $(seq 1 40); do
  if docker exec "$NEWAPI_CONTAINER" wget -qO- http://127.0.0.1:3000/api/status >/dev/null 2>&1; then
    newapi_healthy=1
    break
  fi
  sleep 1
done
if [[ "$newapi_healthy" -ne 1 ]]; then
  echo "newapi_health_failed" >&2
  exit 22
fi

docker exec "$GATEWAY" python -c \
  "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=3)); print(json.dumps({'gateway_ok':d.get('ok')},ensure_ascii=False))"
docker exec "$POSTGRES_CONTAINER" psql -At -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT 'target_channel_status='||status FROM channels WHERE id=$TARGET_CHANNEL_ID;
   SELECT 'target_channel_enabled_abilities='||count(*) FROM abilities WHERE channel_id=$TARGET_CHANNEL_ID AND enabled;
   SELECT 'gpt55_enabled_channels='||string_agg(channel_id::text,',' ORDER BY channel_id) FROM abilities WHERE model='gpt-5.5' AND enabled;"

echo "rollback_container=$rollback_container"
echo "backup_dir=$backup_dir"
echo "database_backup=$database_backup"
