#!/bin/sh
set -eu

current=xtai-video-job-gateway-v2-production
candidate=xtai-video-job-gateway-issue90-candidate
rollback=xtai-video-job-gateway-v2-production-rollback-20260813
image=xtai/video-job-gateway:auto-recovery-20260813
release=/opt/xtai/releases/video-auto-recovery-20260813
state=/opt/xtai/state/video-billing-v2-production/data
candidate_state=/opt/xtai/state/video-billing-v2-issue90-candidate/data
secrets=/opt/xtai/secrets/video-billing
env_file="$release/runtime.env"

docker inspect "$current" > "$release/prod-inspect.json"
chmod 600 "$release/prod-inspect.json"
python3 - "$release/prod-inspect.json" "$env_file" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
values = json.loads(source.read_text(encoding="utf-8"))[0]["Config"]["Env"]
lines = []
for value in values:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise SystemExit("unsafe environment value")
    lines.append(value)
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
target.chmod(0o600)
PY

docker exec "$current" python -c "import sqlite3; a=sqlite3.connect('/data/video-jobs.sqlite3'); b=sqlite3.connect('/data/video-jobs.issue90.backup.sqlite3'); a.backup(b); b.close(); a.close()"
rm -rf "$candidate_state"
mkdir -p "$candidate_state"
cp "$state/video-jobs.issue90.backup.sqlite3" "$candidate_state/video-jobs.sqlite3"
chown -R 10002:10002 "$(dirname "$candidate_state")"
touch "$candidate_state/DRAIN"

docker rm -f "$candidate" >/dev/null 2>&1 || true
docker run -d --name "$candidate" --network app-net --user gateway \
  --env-file "$env_file" \
  -e VIDEO_JOB_GATEWAY_DATA_DIR=/data \
  -e VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED=false \
  -v "$candidate_state:/data" \
  -v "$secrets:/run/secrets/video-billing:ro" \
  "$image" >/dev/null
sleep 3
docker exec "$candidate" python -c "import json,sqlite3,urllib.request; h=json.loads(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=3).read()); assert h['ok']; c=sqlite3.connect('/data/video-jobs.sqlite3'); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; assert c.execute(\"select count(*) from sqlite_master where type='table' and name='video_job_attempts'\").fetchone()[0]==1; print('candidate=ok')"
docker rm -f "$candidate" >/dev/null

touch "$state/DRAIN"
active=$(docker exec "$current" python -c "import sqlite3; c=sqlite3.connect('/data/video-jobs.sqlite3'); print(c.execute(\"select count(*) from video_jobs where status in ('queued','submitting','running','reconciling')\").fetchone()[0])")
pending=$(docker exec "$current" python -c "import sqlite3; c=sqlite3.connect('/data/video-jobs.sqlite3'); print(c.execute(\"select count(*) from video_jobs where billing_status in ('settlement_pending','recovery_pending')\").fetchone()[0])")
if [ "$active" != 0 ] || [ "$pending" != 0 ]; then
  rm -f "$state/DRAIN"
  echo "production not idle: active=$active pending=$pending" >&2
  exit 1
fi

docker rm -f "$rollback" >/dev/null 2>&1 || true
docker stop "$current" >/dev/null
docker rename "$current" "$rollback"

restore() {
  docker rm -f "$current" >/dev/null 2>&1 || true
  docker rename "$rollback" "$current" >/dev/null 2>&1 || true
  rm -f "$state/DRAIN"
  docker start "$current" >/dev/null 2>&1 || true
}
trap restore INT TERM HUP

if ! docker run -d --name "$current" --network app-net --restart unless-stopped --user gateway \
  --env-file "$env_file" \
  -v "$state:/data" \
  -v "$secrets:/run/secrets/video-billing:ro" \
  "$image" >/dev/null; then
  restore
  exit 1
fi
sleep 3
if ! docker exec "$current" python -c "import json,urllib.request; h=json.loads(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=3).read()); assert h['ok']; print('health=ok')"; then
  restore
  exit 1
fi
rm -f "$state/DRAIN"
sleep 2
if ! docker exec "$current" python -c "import json,urllib.request; r=json.loads(urllib.request.urlopen('http://127.0.0.1:8091/ready',timeout=3).read()); assert r['ok'] and r['accepting']; print('ready=ok')"; then
  restore
  exit 1
fi
ln -sfn "$release" /opt/xtai/services-video-job-gateway-current
rm -f "$env_file" "$release/prod-inspect.json"
trap - INT TERM HUP
echo "deployment=ok rollback=$rollback"
