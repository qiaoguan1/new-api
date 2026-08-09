#!/usr/bin/env python3
"""Discover and normalize changing upstream video model catalogs.

Dry-run is the default. ``--apply-snapshot`` updates only catalog/audit JSON;
it never edits NewAPI routes, model mappings, or prices.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MODULE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(MODULE_ROOT))

from upstream_video_catalog import (  # noqa: E402
    CatalogCollectionError,
    build_mapping_report,
    build_route_gates,
    build_trusted_price_evidence,
    fetch_channel_catalog,
    merge_complete_snapshot,
    read_enabled_channels,
    source_for_channel,
)
from video_catalog_policy import (  # noqa: E402
    CatalogPolicyError,
    build_manifests,
    validate_policy,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def read_json(path: pathlib.Path, default=None, *, required=False):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        if required:
            raise CatalogCollectionError(f"cannot read required JSON {path.name}: {exc}") from exc
        return default


def write_json_atomic(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.environ.get("CHANNEL_MONITOR_ROOT", str(MODULE_ROOT)),
        help="channel-monitor runtime directory",
    )
    parser.add_argument(
        "--stack-root",
        default=os.environ.get("CHANNEL_MONITOR_STACK_ROOT", "/opt/ai-api-stack"),
        help="Docker Compose stack directory",
    )
    parser.add_argument("--policy", help="override video model policy path")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate operator policy without contacting upstreams",
    )
    parser.add_argument(
        "--apply-snapshot",
        action="store_true",
        help="atomically update catalog snapshot, run audit, and mapping report",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="include matched and quarantined raw names in stdout",
    )
    return parser.parse_args(argv)


def _summary(merged, report, routes, costs, manifests, *, applied):
    observations = merged["run"]["observations"]
    return {
        "ok": True,
        "mode": "apply_snapshot" if applied else "dry_run",
        "policy_revision": report["policy_revision"],
        "channels_checked": len(observations),
        "channels_complete": len(merged["run"]["complete_channels"]),
        "channels_failed": len(merged["run"]["failed_channels"]),
        "catalog_models_seen": sum(int(row.get("catalog_count") or 0) for row in observations),
        "relevant_raw_models": sum(len(row.get("relevant_models") or []) for row in observations),
        "matched_routes": len(report["matched"]),
        "review_required": len(report["review_required"]),
        "enabled_healthy_routes": len(routes),
        "upstream_cost_rows": len(costs),
        "publishable_routes": len(manifests["internal"]["routes"]),
        "public_models": manifests["public"]["models"],
    }


def run(args) -> dict:
    root = pathlib.Path(args.root).resolve()
    policy_path = pathlib.Path(args.policy).resolve() if args.policy else (
        root / "config" / "video-model-policy.json"
    )
    policy = validate_policy(read_json(policy_path, required=True))
    official_pricing = read_json(
        root / "config" / "official-video-pricing.json", required=True
    )
    if args.validate_only:
        return {
            "ok": True,
            "mode": "validate_only",
            "policy_revision": policy["revision"],
            "approved_rules": sum(
                1
                for rule in policy["rules"]
                if rule["enabled"] and rule["review_state"] == "approved"
            ),
            "publish_allowlist": sorted(
                f"{model}@{resolution}" for model, resolution in policy["_publish_keys"]
            ),
        }

    upstreams = read_json(root / "upstreams.json", required=True)
    if not isinstance(upstreams, list):
        raise CatalogCollectionError("upstreams.json must contain a list")
    channels = read_enabled_channels(args.stack_root)
    collected_at = datetime.now(BEIJING).isoformat(timespec="seconds")
    observations = []
    for channel in channels:
        source = source_for_channel(channel, upstreams)
        if source is None:
            continue
        observations.append(
            fetch_channel_catalog(
                channel,
                source,
                policy,
                collected_at=collected_at,
                timeout=args.timeout,
            )
        )

    data_dir = root / "data"
    snapshot_path = data_dir / "video-catalog-snapshot.json"
    previous = read_json(snapshot_path, {"schema_version": 1, "channels": []})
    merged = merge_complete_snapshot(
        previous,
        observations,
        collected_at=collected_at,
        policy_revision=policy["revision"],
    )
    report = build_mapping_report(merged["snapshot"], policy)
    target_day = (datetime.now(BEIJING).date() - timedelta(days=1)).isoformat()
    daily_audit = read_json(data_dir / "daily-upstream-audit.json", required=True)
    ledger = read_json(data_dir / "upstream-balance-ledger.json", required=True)
    routes = build_route_gates(report, daily_audit, expected_day=target_day)
    costs = build_trusted_price_evidence(
        ledger,
        policy,
        target_day=target_day,
    )
    manifests = build_manifests(
        report["matched"], routes, costs, policy, official_pricing
    )
    if args.apply_snapshot:
        write_json_atomic(snapshot_path, merged["snapshot"])
        write_json_atomic(data_dir / "video-catalog-last-run.json", merged["run"])
        write_json_atomic(data_dir / "video-model-mapping-report.json", report)
        write_json_atomic(data_dir / "video-route-manifest-candidate.json", manifests["internal"])
        write_json_atomic(data_dir / "video-capabilities-candidate.json", manifests["public"])

    result = _summary(
        merged,
        report,
        routes,
        costs,
        manifests,
        applied=args.apply_snapshot,
    )
    if args.print_report:
        result["matched"] = report["matched"]
        result["review_queue"] = report["review_required"]
        result["route_candidates"] = routes
        result["upstream_costs"] = costs
        result["candidate_manifest"] = manifests["internal"]
        result["failures"] = [
            {
                "channel_id": row.get("channel_id"),
                "source": row.get("source"),
                "error": row.get("error"),
            }
            for row in merged["run"]["observations"]
            if row.get("complete") is not True
        ]
    return result


def main(argv=None) -> int:
    try:
        result = run(parse_args(argv))
    except (CatalogCollectionError, CatalogPolicyError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
