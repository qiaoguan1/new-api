#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

NEW_ORIGIN="${NEW_ORIGIN:-https://oh-code.me}"
STACK_DIR="${STACK_DIR:-/opt/ai-api-stack}"
MONITOR_DIR="${MONITOR_DIR:-$STACK_DIR/channel-monitor}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-ai-api-stack-postgres-1}"
NEWAPI_CONTAINER="${NEWAPI_CONTAINER:-ai-api-stack-new-api-1}"

if [[ "$NEW_ORIGIN" != "https://oh-code.me" ]]; then
  echo "unexpected Code Plan origin" >&2
  exit 30
fi

DB_USER="$(docker exec "$POSTGRES_CONTAINER" sh -lc 'printf %s "$POSTGRES_USER"')"
DB_NAME="$(docker exec "$POSTGRES_CONTAINER" sh -lc 'printf %s "$POSTGRES_DB"')"
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$STACK_DIR/backups/codeplan-origin-$stamp"
mkdir -p "$backup_dir"

docker exec "$POSTGRES_CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$backup_dir/newapi.dump"
cp -a "$MONITOR_DIR/upstreams.json" "$backup_dir/upstreams.json"
cp -a "$MONITOR_DIR/upstream-credentials.json" "$backup_dir/upstream-credentials.json"
chmod 600 "$backup_dir/newapi.dump" "$backup_dir/upstream-credentials.json"
docker exec -i "$POSTGRES_CONTAINER" pg_restore -l < "$backup_dir/newapi.dump" >/dev/null
python3 - "$backup_dir" <<'PY'
import json
import pathlib
import sys

backup = pathlib.Path(sys.argv[1])
json.loads((backup / "upstreams.json").read_text(encoding="utf-8"))
json.loads((backup / "upstream-credentials.json").read_text(encoding="utf-8"))
PY

actual_identities="$(docker exec "$POSTGRES_CONTAINER" psql -At -U "$DB_USER" -d "$DB_NAME" -c \
  "select id||':'||name from channels where id in (38,39) order by id")"
expected_identities=$'38:Code Plan · 图片\n39:Code Plan · 文字'
if [[ "$actual_identities" != "$expected_identities" ]]; then
  echo "Code Plan channel identity mismatch" >&2
  exit 31
fi

python3 - "$POSTGRES_CONTAINER" "$NEW_ORIGIN" <<'PY'
import json
import socket
import subprocess
import sys
import urllib.parse

import requests

container, origin = sys.argv[1:]
host = urllib.parse.urlsplit(origin).hostname
if not host or not socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
    raise RuntimeError("Code Plan origin does not resolve")

status = requests.get(origin + "/api/status", timeout=(5, 12))
status.raise_for_status()
status_body = status.json()
if status_body.get("success") is not True:
    raise RuntimeError("Code Plan status endpoint is not healthy")
system_name = str((status_body.get("data") or {}).get("system_name") or "")
if "Code-Plan" not in system_name:
    raise RuntimeError("Code Plan system identity mismatch")

sql = 'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F "|" -c "select id,key from channels where id in (38,39) order by id"'
key_rows = subprocess.check_output(
    ["docker", "exec", container, "sh", "-lc", sql],
    text=True,
).splitlines()
keys = {int(row.split("|", 1)[0]): row.split("|", 1)[1] for row in key_rows}
if not keys.get(38) or not keys.get(39):
    raise RuntimeError("Code Plan channel credential missing")
key = keys[39]
models = requests.get(
    origin + "/v1/models",
    headers={"Authorization": "Bearer " + key},
    timeout=(5, 12),
)
models.raise_for_status()
model_ids = {
    str(row.get("id") or "")
    for row in models.json().get("data", [])
    if isinstance(row, dict)
}
required = {"gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra"}
if not required.issubset(model_ids):
    raise RuntimeError("Code Plan required text models are not authorized")

# One bounded text request is the final restoration gate.  The image probe is
# deliberately invalid (missing prompt), proving auth and endpoint routing
# without creating a billable image task.
chat = requests.post(
    origin + "/v1/chat/completions",
    headers={"Authorization": "Bearer " + key},
    json={
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "回复OK"}],
        "max_tokens": 1,
        "stream": False,
    },
    timeout=(10, 90),
)
if chat.status_code != 200:
    raise RuntimeError(f"Code Plan minimal text verification failed ({chat.status_code})")
image = requests.post(
    origin + "/v1/images/generations",
    headers={"Authorization": "Bearer " + keys[38]},
    json={"model": "gpt-image-2"},
    timeout=(5, 20),
)
image_error_code = ""
try:
    image_error = image.json().get("error") or {}
    image_error_code = str(image_error.get("code") or "") if isinstance(image_error, dict) else ""
except (ValueError, AttributeError):
    pass
if image.status_code != 503 or image_error_code != "model_not_found":
    raise RuntimeError(
        f"Code Plan image entitlement state changed unexpectedly ({image.status_code})"
    )
print(
    json.dumps(
        {
            "origin_verified": True,
            "required_model_count": len(required),
            "minimal_text_verified": True,
            "image_validation_http": image.status_code,
            "image_entitled": False,
        }
    )
)
PY

python3 - "$MONITOR_DIR/upstreams.json" "$MONITOR_DIR/upstream-credentials.json" "$NEW_ORIGIN" <<'PY'
import json
import os
import pathlib
import sys

upstreams_path = pathlib.Path(sys.argv[1])
credentials_path = pathlib.Path(sys.argv[2])
origin = sys.argv[3]

def atomic_json(path: pathlib.Path, payload, mode: int) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

upstreams = json.loads(upstreams_path.read_text(encoding="utf-8"))
rows = upstreams.get("upstreams", upstreams) if isinstance(upstreams, dict) else upstreams
values = list(rows.values()) if isinstance(rows, dict) else rows
matches = [row for row in values if isinstance(row, dict) and row.get("slug") == "codeplan"]
if len(matches) != 1:
    raise RuntimeError("expected exactly one Code Plan upstream")
matches[0]["website_url"] = origin
matches[0]["hosts"] = ["oh-code.me"]

credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
credential = credentials.get("codeplan") if isinstance(credentials, dict) else None
if not isinstance(credential, dict) or not credential.get("username") or not credential.get("password"):
    raise RuntimeError("Code Plan monitor credential missing")
credential["website_url"] = origin

atomic_json(upstreams_path, upstreams, 0o644)
atomic_json(credentials_path, credentials, 0o600)
PY

docker exec -i "$POSTGRES_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" >/dev/null <<'SQL'
BEGIN;
UPDATE channels SET base_url='https://oh-code.me' WHERE id IN (38,39);
UPDATE channels SET status=2 WHERE id=38;
UPDATE channels
SET models='gpt-5.5,gpt-5.6-sol,gpt-5.6-terra', status=1
WHERE id=39;
UPDATE abilities
SET enabled=(model IN ('gpt-5.5','gpt-5.6-sol','gpt-5.6-terra'))
WHERE channel_id=39;
UPDATE abilities SET enabled=false WHERE channel_id=38;
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
  echo "NewAPI did not become healthy" >&2
  exit 32
fi

cd "$MONITOR_DIR"
python3 - <<'PY'
import json
import runpy
import sys

sys.path.insert(0, "scripts")
module = runpy.run_path("scripts/fetch-upstream-balance.py")
credentials = json.loads(open("upstream-credentials.json", encoding="utf-8").read())
result = module["probe_balance"]("codeplan", credentials["codeplan"], "https://oh-code.me")
if result.get("billing_api") != "newapi_classic" or result.get("balance_usd") is None:
    raise RuntimeError("Code Plan balance verification failed")
print(json.dumps({"balance_verified": True, "billing_api": result.get("billing_api")}))
PY

docker exec "$POSTGRES_CONTAINER" psql -At -U "$DB_USER" -d "$DB_NAME" -c \
  "select id||'|'||status||'|'||base_url||'|'||models from channels where id in (38,39) order by id;
   select model||'|'||enabled from abilities where channel_id=39 order by model;"
echo "backup_dir=$backup_dir"
