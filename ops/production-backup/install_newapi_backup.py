#!/usr/bin/env python3
"""Install the verified NewAPI backup worker, cron, and log rotation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


BEGIN_MARKER = "# BEGIN NEWAPI VERIFIED DAILY BACKUP"
END_MARKER = "# END NEWAPI VERIFIED DAILY BACKUP"
CRON_COMMAND = (
    "30 3 * * * umask 077 && cd /opt/ai-api-stack && "
    "/usr/bin/flock -n /run/lock/newapi-daily-backup.lock "
    "/usr/bin/python3 channel-monitor/scripts/newapi-daily-backup.py "
    ">> /var/log/newapi-daily-backup.log 2>&1"
)
LOGROTATE_CONTENT = """/var/log/newapi-daily-backup.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0600 root root
}
"""


def render_crontab(existing: str) -> str:
    """Return an idempotent crontab with one Beijing-time managed block."""
    if "\x00" in existing:
        raise ValueError("crontab contains a NUL byte")
    normalized = existing.replace("\r\n", "\n").replace("\r", "\n")
    begin_count = normalized.count(BEGIN_MARKER)
    end_count = normalized.count(END_MARKER)
    if begin_count != end_count or begin_count > 1:
        raise ValueError("crontab has unbalanced managed markers")
    if begin_count == 1:
        begin = normalized.index(BEGIN_MARKER)
        end = normalized.index(END_MARKER, begin) + len(END_MARKER)
        if normalized[begin:end].count(BEGIN_MARKER) != 1:
            raise ValueError("crontab managed block is malformed")
        normalized = normalized[:begin] + normalized[end:]
    prefix = normalized.strip("\n")
    block = "\n".join(
        (
            BEGIN_MARKER,
            "CRON_TZ=Asia/Shanghai",
            CRON_COMMAND,
            END_MARKER,
        )
    )
    return f"{prefix}\n\n{block}\n" if prefix else f"{block}\n"


def parse_crontab_result(returncode: int, stdout: str, stderr: str) -> str:
    """Return crontab text while refusing ambiguous read failures."""
    if returncode == 0:
        return stdout
    if returncode == 1 and "no crontab for" in stderr.lower() and not stdout:
        return ""
    raise RuntimeError("unable to read root crontab safely")


def _atomic_copy(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_handle, os.fdopen(descriptor, "wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_text(destination: Path, content: str, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(source_script: Path, stack_root: Path) -> Path:
    """Install production artifacts and return the restricted rollback path."""
    if os.geteuid() != 0:
        raise PermissionError("installer must run as root")
    source_script = source_script.resolve()
    stack_root = stack_root.resolve()
    if not source_script.is_file():
        raise FileNotFoundError(source_script)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    rollback = stack_root / "backups" / f"issue26-daily-backup-{timestamp}"
    rollback.mkdir(mode=0o700)
    target_script = stack_root / "channel-monitor" / "scripts" / "newapi-daily-backup.py"
    logrotate_path = Path("/etc/logrotate.d/newapi-daily-backup")

    current = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    current_crontab = parse_crontab_result(
        current.returncode, current.stdout, current.stderr
    )
    crontab_backup = rollback / "root.crontab.before"
    crontab_backup.write_text(current_crontab, encoding="utf-8", newline="\n")
    os.chmod(crontab_backup, 0o600)
    if target_script.exists():
        shutil.copy2(target_script, rollback / "newapi-daily-backup.py.before")
        os.chmod(rollback / "newapi-daily-backup.py.before", 0o600)
    if logrotate_path.exists():
        shutil.copy2(logrotate_path, rollback / "newapi-daily-backup.logrotate.before")
        os.chmod(rollback / "newapi-daily-backup.logrotate.before", 0o600)

    _atomic_copy(source_script, target_script, 0o700)
    _atomic_text(logrotate_path, LOGROTATE_CONTENT, 0o644)
    rendered = render_crontab(current_crontab)
    descriptor, temporary_name = tempfile.mkstemp(prefix="issue26-crontab-", dir="/tmp")
    temporary_crontab = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        os.chmod(temporary_crontab, 0o600)
        subprocess.run(["crontab", str(temporary_crontab)], check=True)
    finally:
        if temporary_crontab.exists():
            temporary_crontab.unlink()

    installed = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=True
    ).stdout
    if installed.replace("\r\n", "\n") != rendered:
        raise RuntimeError("installed crontab does not match the rendered contract")

    evidence = {
        "installed_script": str(target_script),
        "script_sha256": _sha256(target_script),
        "cron_marker": BEGIN_MARKER,
        "logrotate_path": str(logrotate_path),
    }
    evidence_path = rollback / "installation.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(evidence_path, 0o600)
    manifest_lines = [
        f"{_sha256(path)}  {path.name}\n"
        for path in sorted(rollback.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    manifest = rollback / "SHA256SUMS"
    manifest.write_text("".join(manifest_lines), encoding="ascii", newline="\n")
    os.chmod(manifest, 0o600)
    os.chmod(rollback, 0o700)
    return rollback


def main() -> int:
    """Install the production backup worker from an explicit source path."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-script", type=Path, required=True)
    parser.add_argument("--stack-root", type=Path, default=Path("/opt/ai-api-stack"))
    args = parser.parse_args()
    rollback = install(args.source_script, args.stack_root)
    print(f"rollback_dir={rollback}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
