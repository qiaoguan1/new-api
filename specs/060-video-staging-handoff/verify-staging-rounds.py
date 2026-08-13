import json
import sqlite3
import time
import urllib.request


token = __import__("os").environ["VIDEO_JOB_GATEWAY_TOKEN"].strip()


for round_number in range(1, 11):
    health = json.loads(
        urllib.request.urlopen("http://127.0.0.1:8091/health", timeout=3).read()
    )
    ready = json.loads(
        urllib.request.urlopen("http://127.0.0.1:8091/ready", timeout=3).read()
    )
    provider_request = urllib.request.Request(
        "http://127.0.0.1:8091/v1/operations/provider-health",
        headers={"Authorization": f"Bearer {token}"},
    )
    providers = json.loads(urllib.request.urlopen(provider_request, timeout=3).read())
    db = sqlite3.connect("/data/video-jobs.sqlite3")
    integrity = db.execute("pragma integrity_check").fetchone()[0]
    attempts_table = db.execute(
        "select count(*) from sqlite_master where type='table' and name='video_job_attempts'"
    ).fetchone()[0]
    active = db.execute(
        """
        select count(*) from video_jobs
        where status in ('queued','submitting','running','reconciling')
        """
    ).fetchone()[0]
    pending = db.execute(
        """
        select count(*) from video_jobs
        where billing_status in ('settlement_pending','recovery_pending')
        """
    ).fetchone()[0]
    backlog = db.execute(
        "select count(*) from video_webhook_outbox where status<>'delivered'"
    ).fetchone()[0]
    db.close()
    eligible = sum(
        1 for row in providers.get("providers", []) if row.get("eligible_for_new_v21_jobs")
    )
    ok = bool(
        health.get("ok")
        and ready.get("ok")
        and ready.get("accepting")
        and integrity == "ok"
        and attempts_table == 1
        and eligible >= 1
        and active == 0
        and pending == 0
        and backlog == 0
    )
    print(
        json.dumps(
            {
                "round": round_number,
                "ok": ok,
                "eligible_providers": eligible,
                "active": active,
                "pending": pending,
                "webhook_backlog": backlog,
            },
            separators=(",", ":"),
        )
    )
    if not ok:
        raise SystemExit(1)
    time.sleep(1)
