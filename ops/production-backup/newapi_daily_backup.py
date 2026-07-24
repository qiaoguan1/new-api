#!/usr/bin/env python3
"""Create an atomic, verified, root-only NewAPI recovery bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Protocol


COMPLETED_PATTERN = re.compile(r"newapi-\d{8}-\d{6}")
TEMP_PATTERN = re.compile(r"\.newapi-\d{8}-\d{6}\.tmp-\d+")
REQUIRED_RECOVERY_PATHS = (
    Path(".env"),
    Path("docker-compose.yml"),
    Path("nginx/conf.d/default.conf"),
)
OPTIONAL_RECOVERY_PATHS = (
    Path("nginx/auth/channel-monitor.htpasswd"),
    Path("nginx/certs"),
    Path("secrets/wechatpay"),
    Path("channel-monitor/upstreams.json"),
    Path("channel-monitor/report-baseline.json"),
    Path("channel-monitor/upstream-credentials.json"),
    Path("channel-monitor/data/upstream-balance-ledger.json"),
    Path("channel-monitor/data/daily-upstream-audit.json"),
    Path("channel-monitor/data/pricing-options.tsv"),
    Path("channel-monitor/data/auto-pricing-log.json"),
    Path("channel-monitor/scripts/newapi-daily-backup.py"),
)


class Runner(Protocol):
    """Callable compatible with the subprocess runner used by this module."""

    def __call__(self, arguments: list[str], **kwargs: object) -> object: ...


def _is_direct_child(root: Path, candidate: Path) -> bool:
    return candidate.parent.resolve() == root.resolve()


def validate_completed_child(root: Path, candidate: Path) -> Path:
    """Validate a completed backup before any retention deletion."""
    root = root.resolve()
    candidate = candidate.absolute()
    if candidate.is_symlink():
        raise ValueError(f"backup child is a symlink: {candidate}")
    if not _is_direct_child(root, candidate):
        raise ValueError(f"backup child escapes root: {candidate}")
    if not COMPLETED_PATTERN.fullmatch(candidate.name):
        raise ValueError(f"backup child has an unexpected name: {candidate.name}")
    if not candidate.is_dir():
        raise ValueError(f"backup child is not a directory: {candidate}")
    return candidate


def _validate_temp_child(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.absolute()
    if candidate.is_symlink() or not _is_direct_child(root, candidate):
        raise ValueError(f"temporary backup path is unsafe: {candidate}")
    if not TEMP_PATTERN.fullmatch(candidate.name):
        raise ValueError(f"temporary backup name is unsafe: {candidate.name}")
    return candidate


def prune_completed_backups(root: Path, retain: int) -> list[Path]:
    """Remove only completed direct children older than the retention count."""
    if retain < 1:
        raise ValueError("retain must be at least one")
    root = root.resolve()
    completed = sorted(
        (
            child
            for child in root.iterdir()
            if COMPLETED_PATTERN.fullmatch(child.name) and child.is_dir()
        ),
        key=lambda path: path.name,
    )
    removed: list[Path] = []
    for child in completed[:-retain]:
        validated = validate_completed_child(root, child)
        shutil.rmtree(validated)
        removed.append(validated)
    return removed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(directory: Path, files: Iterable[Path]) -> Path:
    """Write a deterministic SHA-256 manifest for direct child files."""
    directory = directory.resolve()
    targets = sorted((path.absolute() for path in files), key=lambda path: path.name)
    lines: list[str] = []
    for target in targets:
        if target.is_symlink():
            raise ValueError(f"manifest target is unsafe: {target}")
        target = target.resolve()
        if target.parent != directory or not target.is_file():
            raise ValueError(f"manifest target is unsafe: {target}")
        lines.append(f"{_sha256(target)}  {target.name}\n")
    manifest = directory / "SHA256SUMS"
    _write_private_text(manifest, "".join(lines))
    return manifest


def verify_manifest(directory: Path) -> None:
    """Verify every entry in a strict direct-child SHA-256 manifest."""
    directory = directory.resolve()
    manifest = directory / "SHA256SUMS"
    for line in manifest.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if match is None:
            raise ValueError("manifest contains an invalid entry")
        expected, name = match.groups()
        target = directory / name
        if target.is_symlink():
            raise ValueError(f"manifest target is unsafe: {name}")
        target = target.resolve()
        if target.parent != directory or not target.is_file():
            raise ValueError(f"manifest target is unsafe: {name}")
        if _sha256(target) != expected:
            raise ValueError(f"manifest checksum mismatch: {name}")


def _write_private_text(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _open_private_binary(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def _resolve_stack_file(stack_root: Path, path: Path) -> Path:
    resolved_root = stack_root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"recovery file escapes stack root: {path}") from error
    return resolved


def recovery_files(stack_root: Path) -> list[Path]:
    """Return regular, non-symlink files from the recovery allowlist."""
    stack_root = stack_root.resolve()
    files: list[Path] = []
    for relative in REQUIRED_RECOVERY_PATHS:
        target = stack_root / relative
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(f"required recovery file is unavailable: {relative}")
        files.append(_resolve_stack_file(stack_root, target))
    for relative in OPTIONAL_RECOVERY_PATHS:
        target = stack_root / relative
        if target.is_symlink():
            continue
        if target.is_file():
            files.append(_resolve_stack_file(stack_root, target))
        elif target.is_dir():
            for child in target.rglob("*"):
                if child.is_file() and not child.is_symlink():
                    files.append(_resolve_stack_file(stack_root, child))
    return sorted(set(files), key=lambda path: str(path.relative_to(stack_root)))


def _write_recovery_archive(stack_root: Path, destination: Path) -> list[str]:
    files = recovery_files(stack_root)
    with _open_private_binary(destination) as output_handle:
        with tarfile.open(
            fileobj=output_handle, mode="w:gz", dereference=False
        ) as archive:
            for source in files:
                archive.add(
                    source, arcname=source.relative_to(stack_root), recursive=False
                )
    return [str(path.relative_to(stack_root)) for path in files]


def run_backup(
    *,
    stack_root: Path,
    backup_root: Path,
    retain: int,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> Path:
    """Create, verify, publish, and rotate one production recovery bundle."""
    stack_root = stack_root.resolve()
    if backup_root.is_symlink():
        raise ValueError("backup root must not be a symlink")
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    backup_root = backup_root.resolve()
    timestamp = now or datetime.now().astimezone()
    suffix = timestamp.strftime("%Y%m%d-%H%M%S")
    final_directory = backup_root / f"newapi-{suffix}"
    temporary_directory = backup_root / f".newapi-{suffix}.tmp-{os.getpid()}"
    if final_directory.exists() or temporary_directory.exists():
        raise FileExistsError("backup destination already exists")
    temporary_directory.mkdir(mode=0o700)

    try:
        dump_path = temporary_directory / "database.pgdump"
        with _open_private_binary(dump_path) as dump_handle:
            runner(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "sh",
                    "-c",
                    'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc',
                ],
                cwd=stack_root,
                check=True,
                stdout=dump_handle,
            )
        with dump_path.open("rb") as dump_handle:
            runner(
                ["docker", "compose", "exec", "-T", "postgres", "pg_restore", "-l"],
                cwd=stack_root,
                check=True,
                stdin=dump_handle,
                stdout=subprocess.DEVNULL,
            )

        archive_path = temporary_directory / "recovery-config.tar.gz"
        archived_files = _write_recovery_archive(stack_root, archive_path)
        metadata_path = temporary_directory / "metadata.json"
        metadata = {
            "created_at": timestamp.isoformat(),
            "database_format": "postgres-custom",
            "database_bytes": dump_path.stat().st_size,
            "recovery_archive_files": archived_files,
            "retention_count": retain,
        }
        _write_private_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        write_manifest(
            temporary_directory,
            [dump_path, archive_path, metadata_path],
        )
        verify_manifest(temporary_directory)
        for child in temporary_directory.iterdir():
            os.chmod(child, 0o600)
        os.replace(temporary_directory, final_directory)
        os.chmod(final_directory, 0o700)
        prune_completed_backups(backup_root, retain=retain)
        return final_directory
    except BaseException:
        if temporary_directory.exists():
            validated_temp = _validate_temp_child(backup_root, temporary_directory)
            shutil.rmtree(validated_temp)
        raise


def main() -> int:
    """Run one backup using production defaults or explicit overrides."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stack-root",
        type=Path,
        default=Path(os.environ.get("AI_API_STACK_ROOT", "/opt/ai-api-stack")),
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path(
            os.environ.get(
                "NEWAPI_BACKUP_ROOT", "/opt/ai-api-stack/backups/daily-newapi"
            )
        ),
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=int(os.environ.get("NEWAPI_BACKUP_RETAIN", "14")),
    )
    args = parser.parse_args()
    result = run_backup(
        stack_root=args.stack_root,
        backup_root=args.backup_root,
        retain=args.retain,
    )
    print(f"backup_complete={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
