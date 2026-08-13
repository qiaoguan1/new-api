#!/bin/sh
set -eu

current=xtai-video-job-gateway-v2-staging
candidate=xtai-video-job-gateway-v2-staging-candidate-issue60
rollback=xtai-video-job-gateway-v2-staging-rollback-issue60
production=xtai-video-job-gateway-v2-production
image=xtai/video-job-gateway:auto-recovery-20260813
release=/opt/xtai/releases/video-auto-recovery-20260813
state=/opt/xtai/state/video-billing-v2-staging/data
candidate_state=/opt/xtai/state/video-billing-v2-staging-candidate-issue60/data
secrets=/opt/xtai/secrets/video-billing
env_file="$release/staging-issue60.env"
staging_inspect="$release/staging-issue60-inspect.json"
production_inspect="$release/production-issue60-inspect.json"

cleanup_sensitive_files() {
  rm -f "$env_file" "$staging_inspect" "$production_inspect"
}
trap cleanup_sensitive_files EXIT

case "$state" in
  /opt/xtai/state/video-billing-v2-staging/data) ;;
  *) echo "unsafe staging state path" >&2; exit 1 ;;
esac
case "$candidate_state" in
  /opt/xtai/state/video-billing-v2-staging-candidate-issue60/data) ;;
  *) echo "unsafe candidate state path" >&2; exit 1 ;;
esac

docker inspect "$current" > "$staging_inspect"
docker inspect "$production" > "$production_inspect"
chmod 600 "$staging_inspect" "$production_inspect"
python3 - "$staging_inspect" "$production_inspect" "$env_file" <<'PY'
import json
import pathlib
import sys

staging = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))[0]
production = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))[0]
target = pathlib.Path(sys.argv[3])

staging_env = dict(item.split("=", 1) for item in staging["Config"]["Env"] if "=" in item)
production_env = dict(item.split("=", 1) for item in production["Config"]["Env"] if "=" in item)
preserve = {
    "VIDEO_JOB_GATEWAY_TOKEN",
    "VIDEO_JOB_GATEWAY_PUBLIC_BASE_URL",
    "VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED",
    "VIDEO_JOB_GATEWAY_WEBHOOK_URL",
    "VIDEO_JOB_GATEWAY_WEBHOOK_SECRET",
}
runtime = {
    key: value
    for key, value in production_env.items()
    if key.startswith("VIDEO_JOB_") and key not in preserve
}
for key in preserve:
    value = staging_env.get(key, "")
    if not value:
        raise SystemExit(f"staging identity setting is missing: {key}")
    runtime[key] = value
runtime["VIDEO_JOB_GATEWAY_DATA_DIR"] = "/data"
runtime["VIDEO_JOB_GATEWAY_HOST"] = "0.0.0.0"
runtime["VIDEO_JOB_GATEWAY_PORT"] = "8091"
for key, value in runtime.items():
    if any(marker in value for marker in ("\n", "\r", "\0")):
        raise SystemExit(f"unsafe environment value: {key}")
target.write_text("\n".join(f"{key}={value}" for key, value in sorted(runtime.items())) + "\n")
target.chmod(0o600)
PY

active=$(docker exec "$current" python -c "import sqlite3; c=sqlite3.connect('/data/video-jobs.sqlite3'); print(c.execute(\"select count(*) from video_jobs where status in ('queued','submitting','running','reconciling')\").fetchone()[0])")
pending=$(docker exec "$current" python -c "import sqlite3; c=sqlite3.connect('/data/video-jobs.sqlite3'); print(c.execute(\"select count(*) from video_jobs where billing_status in ('settlement_pending','recovery_pending')\").fetchone()[0])")
if [ "$active" != 0 ] || [ "$pending" != 0 ]; then
  echo "staging not idle: active=$active pending=$pending" >&2
  exit 1
fi

docker exec "$current" python -c "import sqlite3; a=sqlite3.connect('/data/video-jobs.sqlite3'); b=sqlite3.connect('/data/video-jobs.issue60.backup.sqlite3'); a.backup(b); b.close(); a.close()"
rm -rf "$candidate_state"
mkdir -p "$candidate_state"
cp "$state/video-jobs.issue60.backup.sqlite3" "$candidate_state/video-jobs.sqlite3"
chown -R 10002:10002 "$(dirname "$candidate_state")"
touch "$candidate_state/DRAIN"

docker rm -f "$candidate" >/dev/null 2>&1 || true
docker run -d --name "$candidate" --network app-net --user gateway \
  --env-file "$env_file" \
  -e VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED=false \
  -v "$candidate_state:/data" \
  -v "$secrets:/run/secrets/video-billing:ro" \
  "$image" >/dev/null
sleep 3
docker exec "$candidate" python -c "import json,sqlite3,urllib.request; h=json.loads(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=3).read()); assert h['ok']; c=sqlite3.connect('/data/video-jobs.sqlite3'); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; assert c.execute(\"select count(*) from sqlite_master where type='table' and name='video_job_attempts'\").fetchone()[0]==1; print('candidate=ok')"
docker rm -f "$candidate" >/dev/null

docker rm -f "$rollback" >/dev/null 2>&1 || true
docker stop "$current" >/dev/null
docker rename "$current" "$rollback"

restore() {
  docker rm -f "$current" >/dev/null 2>&1 || true
  docker rename "$rollback" "$current" >/dev/null 2>&1 || true
  docker start "$current" >/dev/null 2>&1 || true
}
trap 'restore; exit 1' INT TERM HUP

if ! docker run -d --name "$current" --network app-net \
  --network-alias video-job-gateway-v2-staging \
  --restart unless-stopped --user gateway \
  --env-file "$env_file" \
  -v "$state:/data" \
  -v "$secrets:/run/secrets/video-billing:ro" \
  "$image" >/dev/null; then
  restore
  exit 1
fi
sleep 3
if ! docker exec "$current" python -c "import json,urllib.request; h=json.loads(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=3).read()); r=json.loads(urllib.request.urlopen('http://127.0.0.1:8091/ready',timeout=3).read()); assert h['ok'] and r['ok'] and r['accepting']; print('staging=ready')"; then
  restore
  exit 1
fi
trap - INT TERM HUP
echo "deployment=ok rollback=$rollback"
