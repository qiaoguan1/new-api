#!/usr/bin/env python3
"""Repeat production invariants after deploying the model-only page."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def http(url: str, method: str = "GET", data: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "model-health-verifier/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def pricing_rows(base_url: str) -> dict[str, dict]:
    status, body = http(f"{base_url}/api/pricing")
    assert status == 200
    root = json.loads(body)
    assert root.get("success") is True
    data = root["data"]
    if isinstance(data, dict):
        for key in ("items", "data", "models"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return {row["model_name"]: row for row in data}


def verify_round(args: argparse.Namespace, round_number: int) -> None:
    status, body = http(f"{args.base_url}/api/status")
    assert status == 200 and json.loads(body).get("success") is True
    rows = pricing_rows(args.base_url)
    assert math.isclose(float(rows["gpt-5.6-sol"]["completion_ratio"]), 6, abs_tol=1e-9)
    assert math.isclose(float(rows["grok-imagine-video-1.5-fast"]["model_price"]), 1.68, abs_tol=1e-9)
    assert http(f"{args.base_url}/api/user/wechatpay/native/pay", "POST", b"{}")[0] == 401

    try:
        urllib.request.urlopen("https://sub.aixingtuyun.com/", timeout=15)
        raise AssertionError("retired Sub2API site must not return success")
    except urllib.error.HTTPError as error:
        assert error.code == 410

    image = command(
        "docker", "inspect", "-f",
        "{{.Config.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}",
        "ai-api-stack-new-api-1",
    )
    assert image == f"{args.expected_image}|running|healthy|0"
    assert len(command("docker", "ps", "--format", "{{.Names}}").splitlines()) == 9

    run = json.loads(args.pricing_log.read_text(encoding="utf-8"))["runs"][-1]
    assert run["date"] == args.pricing_day and run["dry_run"] is False
    assert len(run["decisions"]) == 809
    assert Counter(item["action"] for item in run["decisions"]) == Counter({"skip": 797, "apply": 12})

    source = args.page_source.read_text(encoding="utf-8")
    assert "/api/perf-metrics/summary?hours=${hours}" in source
    assert "每小时汇总，可手动刷新。" in source
    for marker in (
        "/api/channel-monitor", "gross_profit", "gross_margin", "upstream_name",
        "channel_name", "上游运行排行", "每 5 分钟刷新",
    ):
        assert marker not in source, marker

    cron = command("crontab", "-l")
    assert "0 * * * *" in cron and "CRON_TZ=Asia/Shanghai" in cron and "*/5" not in cron
    role = command(
        "docker", "exec", "ai-api-stack-postgres-1", "psql", "-U", "newapi", "-d", "new-api",
        "-Atc", "select role from users where id=65;",
    )
    assert role == "1"
    print(f"ROUND {round_number:02d} PASS | site+pricing+payment+containers+model-only-page+hourly-cron")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--pricing-day", required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--base-url", default="https://api.aixingtuyun.com")
    parser.add_argument(
        "--pricing-log",
        type=Path,
        default=Path("/opt/ai-api-stack/channel-monitor/data/auto-pricing-log.json"),
    )
    parser.add_argument(
        "--page-source",
        type=Path,
        default=Path("/root/new-api-build/new-api/web/default/src/features/channel-monitor/index.tsx"),
    )
    args = parser.parse_args()
    for round_number in range(1, args.rounds + 1):
        verify_round(args, round_number)
    print(f"PRODUCTION_VALIDATION {args.rounds}/{args.rounds} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
