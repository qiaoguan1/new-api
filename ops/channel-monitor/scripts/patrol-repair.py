#!/usr/bin/env python3
"""Run one XingTu relay patrol with bounded self-healing and notification."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import patrol_repair


ROOT = pathlib.Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=pathlib.Path, default=ROOT / "config" / "patrol-repair-policy.json")
    parser.add_argument("--state", type=pathlib.Path, default=ROOT / "data" / "patrol-repair-state.json")
    parser.add_argument("--report", type=pathlib.Path, default=ROOT / "data" / "patrol-repair-latest.json")
    parser.add_argument("--dry-run", action="store_true", help="check only; do not repair, notify, or persist")
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = patrol_repair.load_policy(args.policy)
        state = patrol_repair.read_json(args.state, {"schema_version": 1, "actions": {}, "incidents": {}})
        if not isinstance(state, dict):
            raise patrol_repair.PatrolError("state_invalid")
        now = int(time.time())
        report, updated, events = patrol_repair.run_patrol(
            policy, state, now=now, repair=not args.dry_run and not args.no_repair,
        )
        delivered = []
        notification_failed = False
        if not args.dry_run and not args.no_notify:
            for event in events:
                try:
                    patrol_repair.send_notification(event, os.environ)
                    delivered.append(event)
                except Exception:
                    notification_failed = True
            updated = patrol_repair.record_deliveries(updated, delivered, now=now)
        report["summary"]["notifications_delivered"] = len(delivered)
        report["summary"]["notification_failed"] = notification_failed
        if not args.dry_run:
            patrol_repair.write_private_json(args.report, report)
            patrol_repair.write_private_json(args.state, updated)
        print(json.dumps({"status": "dry_run" if args.dry_run else "complete", **report["summary"]}, sort_keys=True))
        unresolved = report["summary"]["failed"] + report["summary"]["unknown"]
        return 2 if unresolved or notification_failed else 0
    except Exception:
        print(json.dumps({"status": "patrol_failed", "code": "internal_failure"}, sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
