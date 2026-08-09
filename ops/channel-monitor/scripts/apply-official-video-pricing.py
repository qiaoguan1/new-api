#!/usr/bin/env python3
"""Apply video prices from the validated official catalog, never upstream cost."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import time


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MODULE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(MODULE_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from official_video_pricing import (  # noqa: E402
    OfficialVideoPricingError,
    build_official_model_price_plan,
    validate_official_video_pricing,
)
from upstream_video_catalog import CatalogCollectionError  # noqa: E402
from video_catalog_policy import (  # noqa: E402
    CatalogPolicyError,
    normalize_model_name,
    validate_policy,
)


def _load_generic_pricing_module():
    path = SCRIPT_DIR / "auto-apply-pricing.py"
    spec = importlib.util.spec_from_file_location("channel_monitor_auto_pricing", path)
    if spec is None or spec.loader is None:
        raise OfficialVideoPricingError("cannot load shared pricing database helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: pathlib.Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise OfficialVideoPricingError(f"cannot read required JSON {path}: {exc}") from exc


def build_official_routes(mapping_report, daily_audit, policy, *, expected_day):
    """Return all enabled configured reviewed video routes, independent of health."""
    checked = validate_policy(policy)
    if not isinstance(mapping_report, dict):
        raise OfficialVideoPricingError("video mapping report must be a JSON object")
    if mapping_report.get("policy_revision") != checked["revision"]:
        raise OfficialVideoPricingError("video mapping report policy revision is stale")
    if not isinstance(daily_audit, dict) or daily_audit.get("date") != expected_day:
        raise OfficialVideoPricingError("daily video inventory is stale")
    mappings = {}
    for row in mapping_report.get("matched", []):
        if not isinstance(row, dict) or row.get("status") != "matched":
            continue
        key = (row.get("channel_id"), row.get("source"), row.get("raw_model"))
        mappings[key] = row

    result = []
    for channel in daily_audit.get("channels") or []:
        if not isinstance(channel, dict) or channel.get("status") != 1:
            continue
        channel_id = channel.get("channel_id")
        source = channel.get("upstream_slug")
        for raw_model in channel.get("configured_models") or []:
            current = normalize_model_name(source, raw_model, checked)
            if current.get("status") != "matched":
                continue
            key = (channel_id, source, raw_model)
            mapping = mappings.get(key)
            if mapping is None:
                raise OfficialVideoPricingError(
                    f"reviewed video route is missing from mapping report: {channel_id}:{raw_model}"
                )
            identity = (mapping.get("stable_model"), mapping.get("resolution"))
            if identity != (
                current.get("stable_model"),
                current.get("resolution"),
            ):
                raise OfficialVideoPricingError("reviewed video mapping no longer matches policy")
            result.append(
                {
                    "channel_id": channel_id,
                    "source": source,
                    "raw_model": raw_model,
                    "stable_model": identity[0],
                    "resolution": identity[1],
                }
            )
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.environ.get("CHANNEL_MONITOR_ROOT", str(MODULE_ROOT)),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    generic = _load_generic_pricing_module()
    generic.ROOT = root
    generic.BACKUP_DIR = root / "backups" / "pricing"
    generated_at = int(time.time())
    try:
        official = validate_official_video_pricing(
            read_json(root / "config" / "official-video-pricing.json")
        )
        policy = validate_policy(read_json(root / "config" / "video-model-policy.json"))
        mapping_report = read_json(root / "data" / "video-model-mapping-report.json")
        daily_audit = read_json(root / "data" / "daily-upstream-audit.json")
        day = generic.target_beijing_day()
        routes = build_official_routes(
            mapping_report, daily_audit, policy, expected_day=day
        )
        current = {
            key: generic.get_option(key)
            for key in generic.OPTION_KEYS + ("GroupRatio",)
        }
        plan = build_official_model_price_plan(official, routes, current)
        changed = any(
            plan["options"][key] != current[key] for key in generic.OPTION_KEYS
        )
        run = {
            "date": day,
            "generated_at": generated_at,
            "dry_run": args.dry_run,
            "changed": changed,
            "pricing_revision": plan["pricing_revision"],
            "markup": plan["markup"],
            "decisions": plan["decisions"],
        }
        if changed and not args.dry_run:
            run["backup_path"] = str(
                generic.backup_pricing_options(
                    day, {key: current[key] for key in generic.OPTION_KEYS}
                )
            )
            run["database_output"] = generic.atomic_update_options(
                plan["options"], {key: current[key] for key in generic.OPTION_KEYS}
            )
        log_path = root / "data" / "official-video-pricing-log.json"
        history = generic.read_json(log_path, {"runs": []})
        runs = history.get("runs") if isinstance(history, dict) else []
        history = {"runs": (runs if isinstance(runs, list) else [])[-89:] + [run]}
        generic.write_json(log_path, history)
        print(json.dumps(run, ensure_ascii=False, sort_keys=True))
        return 0
    except (
        CatalogCollectionError,
        CatalogPolicyError,
        OfficialVideoPricingError,
        generic.PricingError,
        OSError,
        ValueError,
    ) as exc:
        failure = {
            "generated_at": generated_at,
            "dry_run": args.dry_run,
            "status": "failed",
            "error": str(exc),
        }
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
