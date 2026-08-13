"""Replay one existing delivered staging event with a fresh HMAC signature."""

import hashlib
import hmac
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request


db = sqlite3.connect("/data/video-jobs.sqlite3")
db.row_factory = sqlite3.Row
event = db.execute(
    """
    select event_id,payload_json,attempts from video_webhook_outbox
    where status='delivered' order by delivered_at desc limit 1
    """
).fetchone()
if not event:
    raise SystemExit("no delivered staging event is available for safe replay")
body = str(event["payload_json"]).encode("utf-8")
payload = json.loads(body)
if str(payload.get("event_id") or "") != str(event["event_id"]):
    raise SystemExit("persisted event identity mismatch")
data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
billing = data.get("billing") if isinstance(data.get("billing"), dict) else {}
contract = str(billing.get("contract_version") or "")
if contract not in {"xtai-video-billing-v2", "xtai-video-billing-v2.1"}:
    raise SystemExit("persisted event contract is not replayable")

target = os.environ["VIDEO_JOB_GATEWAY_WEBHOOK_URL"].strip()
secret = os.environ["VIDEO_JOB_GATEWAY_WEBHOOK_SECRET"].strip().encode("utf-8")
timestamp = str(int(time.time()))
signature = "v1=" + hmac.new(
    secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256
).hexdigest()
request = urllib.request.Request(
    target,
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-XingTu-Contract-Version": contract,
        "X-XingTu-Event-Id": str(event["event_id"]),
        "X-XingTu-Timestamp": timestamp,
        "X-XingTu-Delivery-Attempt": str(int(event["attempts"] or 0) + 1),
        "X-XingTu-Signature": signature,
        "User-Agent": "XingTuVideoWebhookVerification/1",
    },
)
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        status = int(response.status)
        response.read(4096)
except urllib.error.HTTPError as error:
    status = int(error.code)
if not 200 <= status < 300:
    raise SystemExit(f"staging webhook replay failed with HTTP {status}")
print(json.dumps({"ok": True, "http_status": status, "duplicate_event_replay": True}))
