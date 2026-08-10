#!/usr/bin/env python3
"""Idempotently merge video-monitor snapshots into the protected monitor payload."""

from __future__ import annotations

import pathlib
import sys


MARKER = "# video-consumption-reconciliation-v1"
ANCHOR = '    payload["daily_history"] = update_daily_history(payload)\n'
INJECTION = f'''    {MARKER}
    video_private = load_json(ROOT / "data" / "video-consumption-private.json", {{}})
    video_public = load_json(ROOT / "data" / "video-model-health-public.json", {{}})
    payload["video_consumption"] = video_private if isinstance(video_private, dict) else {{}}
    payload["public_video_models"] = (
        video_public.get("models", []) if isinstance(video_public, dict) else []
    )

'''


def patch_text(source: str) -> str:
    """Return generator source with one bounded snapshot merge inserted."""
    if MARKER in source:
        return source
    if ANCHOR not in source:
        raise RuntimeError("generate-monitor-data.py anchor was not found")
    if "ROOT =" not in source or "def load_json(" not in source:
        raise RuntimeError("generate-monitor-data.py JSON loader contract was not found")
    return source.replace(ANCHOR, INJECTION + ANCHOR, 1)


def main() -> int:
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(
        "/opt/ai-api-stack/channel-monitor/scripts/generate-monitor-data.py"
    )
    source = target.read_text(encoding="utf-8")
    patched = patch_text(source)
    if patched != source:
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(patched, encoding="utf-8")
        temporary.replace(target)
        print(f"patched {target}")
    else:
        print(f"already patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
