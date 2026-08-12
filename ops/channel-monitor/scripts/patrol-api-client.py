#!/usr/bin/env python3
"""Root-only local client for the XingTu patrol control API."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import patrol_api


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("status", "run"))
    parser.add_argument("--token-file", type=pathlib.Path, default=pathlib.Path("/etc/channel-monitor-patrol-api.token"))
    args = parser.parse_args(argv)
    try:
        token = patrol_api.read_token(args.token_file)
        result = patrol_api.call_local_api(token, args.operation)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"success": False, "code": "local_api_call_failed"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
