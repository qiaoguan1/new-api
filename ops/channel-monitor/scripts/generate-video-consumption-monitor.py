#!/usr/bin/env python3
"""Generate private provider reconciliation and provider-neutral video health snapshots."""

from __future__ import annotations

import json
import os
import pathlib
import sys

from monitor_time import beijing_iso_now, resolve_beijing_business_day
from video_consumption import (
    build_monitor_snapshots,
    gateway_rows_from_sqlite,
    reconcile_video_usage,
)


ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "upstream-balance-ledger.json"
PRIVATE_PATH = ROOT / "data" / "video-consumption-private.json"
PUBLIC_PATH = ROOT / "data" / "video-model-health-public.json"
GATEWAY_DB = pathlib.Path(
    os.environ.get(
        "VIDEO_GATEWAY_DB",
        "/opt/xtai/state/video-job-gateway/data/video-jobs.sqlite3",
    )
)
VIDEO_PROVIDERS = {"toonflow", "paisio"}


def read_json(path: pathlib.Path, fallback):
    """Read JSON or return the supplied fallback."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def atomic_write_json(path: pathlib.Path, value) -> None:
    """Atomically replace one JSON snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build(day: str | None = None, *, gateway_db: pathlib.Path | None = None):
    """Build deterministic daily snapshots from the ledger and read-only gateway database."""
    business_day = day or resolve_beijing_business_day(os.environ.get("CHANNEL_MONITOR_DAY", ""))
    ledger = read_json(LEDGER_PATH, {"days": {}})
    entries = ((ledger.get("days") or {}).get(business_day) or {})
    video_entries = {
        provider: entry
        for provider, entry in entries.items()
        if provider in VIDEO_PROVIDERS and isinstance(entry, dict)
    }
    evidence = []
    for provider, entry in video_entries.items():
        for row in entry.get("video_task_evidence") or []:
            if not isinstance(row, dict):
                continue
            sanitized = dict(row)
            sanitized["provider_id"] = provider
            evidence.append(sanitized)
    jobs = gateway_rows_from_sqlite(gateway_db or GATEWAY_DB, business_day)
    reconciled = reconcile_video_usage(jobs, evidence)
    return build_monitor_snapshots(
        business_day,
        reconciled,
        generated_at=beijing_iso_now(),
        collection_entries=video_entries,
    )


def main() -> int:
    snapshots = build()
    atomic_write_json(PRIVATE_PATH, snapshots["private"])
    atomic_write_json(PUBLIC_PATH, snapshots["public"])
    print(
        json.dumps(
            {
                "date": snapshots["private"]["date"],
                "providers": len(snapshots["private"]["providers"]),
                "models": len(snapshots["public"]["models"]),
                "reconciled_jobs": len(snapshots["private"]["reconciliation"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
