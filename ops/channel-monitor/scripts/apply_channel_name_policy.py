#!/usr/bin/env python3
"""Audit or atomically apply the canonical channel-name policy."""

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess

from channel_name_policy import CHANNEL_NAMES, migration_sql, validate_inventory


POSTGRES_CONTAINER = os.environ.get(
    "CHANNEL_MONITOR_POSTGRES", "ai-api-stack-postgres-1"
)
DB_USER = os.environ.get("CHANNEL_MONITOR_DB_USER", "newapi")
DB_NAME = os.environ.get("CHANNEL_MONITOR_DB_NAME", "new-api")
BACKUP_ROOT = Path(
    os.environ.get(
        "CHANNEL_NAME_BACKUP_ROOT",
        "/opt/ai-api-stack/backups/channel-name-policy",
    )
)


def run_psql(sql):
    process = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            POSTGRES_CONTAINER,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            DB_USER,
            "-d",
            DB_NAME,
            "-At",
        ],
        input=sql,
        text=True,
        capture_output=True,
    )
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout).strip())
    return process.stdout.strip()


def load_inventory():
    raw = run_psql(
        "SELECT coalesce(json_agg(json_build_object('id', id, 'name', name) "
        "ORDER BY id), '[]'::json)::text FROM channels;"
    )
    rows = json.loads(raw or "[]")
    if not isinstance(rows, list):
        raise RuntimeError("channel inventory query did not return a list")
    return rows


def write_backup(rows):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BACKUP_ROOT, 0o700)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    target = BACKUP_ROOT / f"channel-names-{timestamp}.json"
    temporary = target.with_suffix(".tmp")
    payload = {
        "created_at": timestamp,
        "before": rows,
        "policy": [
            {"id": channel_id, "old_name": old, "new_name": new}
            for channel_id, (old, new) in CHANNEL_NAMES.items()
        ],
    }
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    before = load_inventory()
    state = validate_inventory(before)
    if not args.apply:
        print(json.dumps({"channels": len(before), "state": state}, sort_keys=True))
        return
    backup = write_backup(before)
    run_psql(migration_sql())
    after = load_inventory()
    if validate_inventory(after) != "applied":
        raise RuntimeError("channel names remain pending after committed migration")
    after_by_id = {int(row["id"]): row["name"] for row in after}
    changed = sum(
        1
        for row in before
        if row["name"] != after_by_id[int(row["id"])]
    )
    print(
        json.dumps(
            {
                "backup": str(backup),
                "changed": changed,
                "channels": len(after),
                "state": "applied",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
