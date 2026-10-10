#!/usr/bin/env python3
"""Create an atomic, verified, root-only NewAPI recovery bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, NamedTuple, Protocol


COMPLETED_PATTERN = re.compile(r"newapi-\d{8}-\d{6}")
TEMP_PATTERN = re.compile(r"\.newapi-\d{8}-\d{6}\.tmp-\d+")
ROOT_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SQLITE_HEADER = b"SQLite format 3\x00"
DEFAULT_MINIMUM_FREE_BYTES = 64 * 1024 * 1024
DEFAULT_MAXIMUM_SNAPSHOT_BYTES = 32 * 1024 * 1024 * 1024
MAXIMUM_SOURCE_FILES = 100000
REQUIRED_RUNTIME_CONTAINERS = (
    "ai-api-stack-new-api-1", "ai-api-stack-postgres-1", "ai-api-stack-nginx-1",
    "ai-api-stack-redis-1",
    "xtai-image-job-gateway-image-job-gateway-1", "xtai-banana-chat-adapter",
    "xtai-nodyhub-image-adapter", "xtai-image25-adapter", "xtai-toonflow-image-adapter",
    "xtai-video-public-execution", "xtai-video-job-gateway-v2-production",
    "xtai-video-job-gateway-video-job-gateway-1",
    "xtai-public-video-catalog176", "media_upload-media-upload-1",
)
AUXILIARY_RUNTIME_CONTAINERS = (
    "ai-api-stack-xt-egress-1", "ai-api-stack-cli-proxy-api-1",
    "ai-api-stack-gpt-image-2-webui-1", "ai-api-stack-searxng-1",
)
PRODUCTION_RUNTIME_CONTAINERS = REQUIRED_RUNTIME_CONTAINERS + AUXILIARY_RUNTIME_CONTAINERS
MAXIMUM_RUNTIME_DESCRIPTOR_BYTES = 8 * 1024 * 1024
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
    Path("channel-monitor/data/daily-ops-digest-state.json"),
    Path("channel-monitor/data/upstream-balance-live.json"),
    Path("channel-monitor/data/upstream-balance-health.json"),
    Path("channel-monitor/data/daily-cost-history.json"),
    Path("channel-monitor/data/daily-price-baseline.json"),
    Path("channel-monitor/data/monitor-data.json"),
    Path("channel-monitor/data/upstream-recharge-summary.json"),
    Path("channel-monitor/config/operator-weekly-review.json"),
    Path("channel-monitor/config/video-model-policy.json"),
    Path("channel-monitor/config/pricing-evidence-overrides.json"),
    Path("channel-monitor/config/production-backup-roots.json"),
    Path("channel-monitor/scripts/newapi-daily-backup.py"),
)


class OptionalExternalRecoveryRoot(NamedTuple):
    """An explicitly authorized directory/file; absent optional roots are reported."""

    name: str
    path: Path
    required: bool = False
    required_sqlite: tuple[str, ...] = ()


class RecoverySourceDriftError(RuntimeError):
    """Preserve private source attribution without disclosing it in log messages."""

    def __init__(self, message: str, source_path: Path, phase: str) -> None:
        super().__init__(message)
        self.source_path = source_path
        self.phase = phase


class SnapshotBudget:
    """Bound elapsed time, staged bytes, and free-space reserve for one bundle."""

    def __init__(self, seconds: float, maximum_bytes: int, minimum_free_bytes: int) -> None:
        if (not math.isfinite(seconds) or seconds <= 0 or type(maximum_bytes) is not int
            or maximum_bytes <= 0 or type(minimum_free_bytes) is not int or minimum_free_bytes < 0):
            raise ValueError("backup budgets must be positive")
        self.deadline = time.monotonic() + seconds
        self.maximum_bytes = maximum_bytes
        self.minimum_free_bytes = minimum_free_bytes
        self.staged_bytes = 0

    def remaining_seconds(self) -> float:
        """Return the remaining wall-clock budget or abort the incomplete bundle."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("backup snapshot deadline exceeded")
        return remaining

    def check_space(self, directory: Path, required_bytes: int = 0) -> None:
        """Keep a reserve on the destination disk, before publishing or copying."""
        self.remaining_seconds()
        if required_bytes < 0 or required_bytes + self.staged_bytes > self.maximum_bytes:
            raise ValueError("backup snapshot exceeds configured byte limit")
        if shutil.disk_usage(directory).free < self.minimum_free_bytes + required_bytes:
            raise OSError("insufficient backup space")


class BoundedSnapshotReader:
    """Enforce time/space limits inside tarfile's streaming copy of large assets."""

    def __init__(self, handle: BinaryIO, budget: SnapshotBudget, directory: Path) -> None:
        self.handle = handle
        self.budget = budget
        self.directory = directory
        self.unchecked_bytes = 0

    def read(self, size: int = -1) -> bytes:
        """Read one bounded archive block without loading a complete media file."""
        self.budget.remaining_seconds()
        block = self.handle.read(size)
        self.unchecked_bytes += len(block)
        if self.unchecked_bytes >= 64 * 1024 * 1024:
            self.budget.check_space(self.directory)
            self.unchecked_bytes = 0
        return block


def _safe_source_path(path: Path) -> Path:
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise ValueError("recovery source must not contain parent traversal")
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise ValueError("recovery source contains a symlink")
    return absolute


def _regular_stat(path: Path) -> os.stat_result:
    _safe_source_path(path)
    result = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(result.st_mode):
        raise ValueError("recovery source is not a regular file")
    return result


def load_recovery_scope(path: Path) -> tuple[tuple[OptionalExternalRecoveryRoot, ...], tuple[str, ...]]:
    """Read one root-only source for data roots and fixed-allowlist runtime scope."""
    source = _safe_source_path(path)
    attributes = _regular_stat(source)
    if attributes.st_size > 1024 * 1024:
        raise ValueError("recovery target configuration is too large")
    if os.name != "nt" and (attributes.st_uid != 0 or attributes.st_mode & 0o077):
        raise PermissionError("recovery target configuration must be root-only")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or not {"schema", "roots"}.issubset(payload)
        or not set(payload).issubset({"schema", "roots", "runtime_containers"})):
        raise ValueError("invalid recovery target configuration")
    if payload["schema"] != "xtai-production-backup-roots-v1" or not isinstance(payload["roots"], list):
        raise ValueError("invalid recovery target schema")
    roots: list[OptionalExternalRecoveryRoot] = []
    seen: set[str] = set()
    for item in payload["roots"]:
        if (not isinstance(item, dict) or not {"name", "path", "required"}.issubset(item)
            or not set(item).issubset({"name", "path", "required", "required_sqlite"})):
            raise ValueError("invalid recovery target entry")
        name, source_path, required = item["name"], item["path"], item["required"]
        if not isinstance(name, str) or not ROOT_NAME_PATTERN.fullmatch(name) or name in seen:
            raise ValueError("invalid or duplicate recovery target name")
        if not isinstance(source_path, str) or not Path(source_path).is_absolute() or type(required) is not bool:
            raise ValueError("invalid recovery target path or required flag")
        sqlite_files = item.get("required_sqlite", [])
        if not isinstance(sqlite_files, list) or not all(isinstance(relative, str) for relative in sqlite_files):
            raise ValueError("invalid required SQLite file list")
        roots.append(OptionalExternalRecoveryRoot(name, Path(source_path), required, tuple(sqlite_files)))
        seen.add(name)
    runtime = payload.get("runtime_containers", list(PRODUCTION_RUNTIME_CONTAINERS))
    if (not isinstance(runtime, list) or not all(isinstance(name, str) for name in runtime)
        or len(set(runtime)) != len(runtime) or not set(runtime).issubset(PRODUCTION_RUNTIME_CONTAINERS)
        or not set(REQUIRED_RUNTIME_CONTAINERS).issubset(runtime)):
        raise ValueError("runtime scope must contain all core containers and only allowed auxiliaries")
    return tuple(roots), tuple(runtime)


def load_external_recovery_roots(path: Path) -> tuple[OptionalExternalRecoveryRoot, ...]:
    """Return explicit roots from the protected recovery policy for library callers."""
    return load_recovery_scope(path)[0]


def _external_files(root: OptionalExternalRecoveryRoot, budget: SnapshotBudget | None = None) -> list[Path]:
    if not ROOT_NAME_PATTERN.fullmatch(root.name) or not root.path.is_absolute() or type(root.required) is not bool:
        raise ValueError("invalid external recovery root")
    source = _safe_source_path(root.path)
    if not source.exists():
        if root.required:
            raise FileNotFoundError("required external recovery root is unavailable")
        return []
    if source.is_file():
        if root.required_sqlite:
            raise ValueError("required SQLite paths need a directory root")
        return [source]
    if not source.is_dir():
        raise ValueError("external recovery root is not a regular file or directory")
    for relative in root.required_sqlite:
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.replace("\\", "/").split("/"))):
            raise ValueError("invalid required SQLite relative path")
        required_source = _safe_source_path(source / relative)
        if not required_source.is_file():
            raise FileNotFoundError("required SQLite recovery source is unavailable")
        if not _sqlite_source(required_source):
            raise ValueError("required SQLite recovery source has an invalid header")
    files: list[Path] = []
    for current, directories, filenames in os.walk(source, followlinks=False):
        for name in (*directories, *filenames):
            if budget is not None:
                budget.remaining_seconds()
            candidate = _safe_source_path(Path(current) / name)
            if candidate.is_dir():
                continue
            try:
                _regular_stat(candidate)
            except FileNotFoundError:
                if _verified_sqlite_sidecar(candidate):
                    continue
                raise
            if _verified_sqlite_sidecar(candidate):
                # The online DB snapshot contains committed WAL data. These
                # temporary files can disappear as the last connection closes.
                continue
            files.append(candidate)
            if len(files) > MAXIMUM_SOURCE_FILES:
                raise ValueError("recovery root exceeds the file-count limit")
    return sorted(files, key=lambda item: item.relative_to(source).as_posix())


def _source_fingerprint(attributes: os.stat_result) -> tuple[int, int, int, int]:
    return attributes.st_dev, attributes.st_ino, attributes.st_size, attributes.st_mtime_ns


def _directory_recovery_metadata(root: OptionalExternalRecoveryRoot, budget: SnapshotBudget) -> list[dict[str, object]]:
    if not root.path.is_dir():
        return []
    result: list[dict[str, object]] = []
    for current, directories, _ in os.walk(root.path, followlinks=False):
        budget.remaining_seconds()
        directory = _safe_source_path(Path(current))
        for name in directories:
            _safe_source_path(directory / name)
        attributes = directory.stat(follow_symlinks=False)
        result.append({"relative_path": directory.relative_to(root.path).as_posix(),
            "source_uid": attributes.st_uid, "source_gid": attributes.st_gid,
            "source_mode": stat.S_IMODE(attributes.st_mode)})
        if len(result) > MAXIMUM_SOURCE_FILES:
            raise ValueError("recovery root exceeds the directory-count limit")
    return sorted(result, key=lambda item: str(item["relative_path"]))


def _copy_stable_file(source: Path, destination: Path, budget: SnapshotBudget) -> os.stat_result:
    before = _regular_stat(source)
    budget.check_space(destination.parent, before.st_size)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as input_handle, _open_private_binary(destination) as output_handle:
        if _source_fingerprint(os.fstat(input_handle.fileno())) != _source_fingerprint(before):
            raise RecoverySourceDriftError("recovery source changed before capture", source, "file-copy")
        copied = 0
        for block in iter(lambda: input_handle.read(1024 * 1024), b""):
            budget.check_space(destination.parent, len(block))
            copied += len(block)
            if copied > before.st_size:
                raise RecoverySourceDriftError("recovery source grew during capture", source, "file-copy")
            output_handle.write(block)
        output_handle.flush()
        os.fsync(output_handle.fileno())
        if copied != before.st_size or _source_fingerprint(os.fstat(input_handle.fileno())) != _source_fingerprint(before):
            raise RecoverySourceDriftError("recovery source changed during capture", source, "file-copy")
    if _source_fingerprint(_regular_stat(source)) != _source_fingerprint(before):
        raise RecoverySourceDriftError("recovery source was replaced during capture", source, "file-copy")
    budget.staged_bytes += copied
    return before


def _sqlite_source(path: Path) -> bool:
    _regular_stat(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        header = handle.read(len(SQLITE_HEADER))
    result = header == SQLITE_HEADER
    if not result and path.suffix in {".sqlite", ".sqlite3"}:
        raise ValueError("SQLite recovery source has an invalid header")
    return result


def _verified_sqlite_sidecar(path: Path) -> bool:
    """Identify transient sidecars only from an existing, header-verified DB."""
    _safe_source_path(path)
    for suffix in ("-wal", "-shm", "-journal"):
        if path.name.endswith(suffix):
            original = _safe_source_path(Path(str(path)[:-len(suffix)]))
            if not original.is_file():
                return False
            return _sqlite_source(original)
    return False


def _sqlite_sidecar(path: Path, sqlite_paths: set[Path]) -> bool:
    for suffix in ("-wal", "-shm", "-journal"):
        if path.name.endswith(suffix):
            original = Path(str(path)[:-len(suffix)])
            if original in sqlite_paths:
                return True
            if original.suffix in {".sqlite", ".sqlite3", ".db"}:
                raise ValueError("orphan SQLite sidecar has no verified database snapshot")
    return False


def _snapshot_sqlite(source: Path, destination: Path, budget: SnapshotBudget) -> os.stat_result:
    before = _regular_stat(source)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(source) + suffix)
        _safe_source_path(sidecar)
        if sidecar.exists():
            try:
                _regular_stat(sidecar)
            except FileNotFoundError:
                if not _verified_sqlite_sidecar(sidecar):
                    raise
    with _open_private_binary(destination):
        pass
    source_connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
    destination_connection = sqlite3.connect(destination, timeout=5)
    try:
        page_size = int(source_connection.execute("PRAGMA page_size").fetchone()[0])

        def progress(_status: int, _remaining: int, total: int) -> None:
            budget.check_space(destination.parent)
            if total * page_size + budget.staged_bytes > budget.maximum_bytes:
                raise ValueError("SQLite snapshot exceeds configured byte limit")

        source_connection.backup(destination_connection, pages=256, progress=progress, sleep=0.05)
        destination_connection.set_progress_handler(lambda: int(time.monotonic() >= budget.deadline), 1000)
        if destination_connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("SQLite recovery snapshot failed integrity validation")
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    after = _regular_stat(source)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise RecoverySourceDriftError("SQLite recovery source was replaced during capture", source, "sqlite-snapshot")
    with destination.open("rb+") as handle:
        os.fsync(handle.fileno())
    os.chmod(destination, 0o600)
    budget.staged_bytes += destination.stat().st_size
    budget.check_space(destination.parent)
    return before


class Runner(Protocol):
    """Callable compatible with the subprocess runner used by this module."""

    def __call__(self, arguments: list[str], **kwargs: object) -> object: ...


def postgresql_database_size(stack_root: Path, runner: Runner = subprocess.run) -> int:
    """Query current DB size under read-only mode; never expose database credentials."""
    result = runner([
        "docker", "compose", "exec", "-T", "postgres", "sh", "-c",
        'exec env PGOPTIONS="-c default_transaction_read_only=on" psql '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc '
        '"SELECT pg_database_size(current_database())"',
    ], cwd=_safe_source_path(stack_root), check=True, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=60)
    output = getattr(result, "stdout", None)
    if isinstance(output, bytes):
        output = output.decode("ascii", errors="strict")
    if not isinstance(output, str) or re.fullmatch(r"[0-9]{1,20}", output.strip()) is None:
        raise ValueError("PostgreSQL size preflight returned an invalid value")
    size = int(output.strip())
    if size <= 0:
        raise ValueError("PostgreSQL size preflight returned an empty database")
    return size


def _capture_runtime_descriptors(
    names: tuple[str, ...], destination: Path, stack_root: Path, budget: SnapshotBudget,
    runner: Runner,
) -> list[dict[str, object]]:
    if not names or len(set(names)) != len(names) or not set(names).issubset(PRODUCTION_RUNTIME_CONTAINERS):
        raise ValueError("runtime descriptor targets are outside the fixed allowlist")
    raw = destination.with_suffix(".raw")
    try:
        with _open_private_binary(raw) as output:
            runner(["docker", "inspect", *names], cwd=stack_root, check=True,
                stdout=output, stderr=subprocess.DEVNULL, timeout=budget.remaining_seconds())
            output.flush()
            os.fsync(output.fileno())
        if raw.stat().st_size > MAXIMUM_RUNTIME_DESCRIPTOR_BYTES:
            raise ValueError("runtime descriptor exceeds configured size limit")
        items = json.loads(raw.read_text(encoding="utf-8"))
        if not isinstance(items, list) or len(items) != len(names):
            raise ValueError("runtime descriptor inventory is incomplete")
        observed: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("Name"), str):
                raise ValueError("runtime descriptor has an invalid container identity")
            name = item["Name"].removeprefix("/")
            if name not in names or name in observed:
                raise ValueError("runtime descriptor inventory differs from the allowlist")
            observed.add(name)
            if (re.fullmatch(r"[0-9a-f]{64}", str(item.get("Id", ""))) is None
                or re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("Image", ""))) is None
                or not isinstance(item.get("Config"), dict) or not isinstance(item.get("HostConfig"), dict)
                or not isinstance(item.get("Mounts"), list) or not isinstance(item.get("NetworkSettings"), dict)
                or not isinstance(item.get("State"), dict)):
                raise ValueError("runtime descriptor lacks immutable recovery configuration")
        saved = {"schema": "xtai-runtime-recovery-v1",
            "captured_at": datetime.now().astimezone().isoformat(), "containers": items}
        _write_private_text(destination, json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n")
        return items
    finally:
        if raw.exists():
            raw.unlink()


def _runtime_recovery_identity(items: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        identity = {name: item.get(name) for name in (
            "Id", "Image", "Config", "HostConfig", "Mounts", "NetworkSettings")}
        # Docker inspect emits Mounts in nondeterministic order. Compare every
        # complete record, including future fields; do not omit mount attributes
        # or normalize ordered Config/Env/network parameters without evidence.
        mounts = item.get("Mounts")
        if not isinstance(mounts, list):
            raise ValueError("runtime recovery mounts must be a list")
        identity["Mounts"] = sorted(mounts, key=lambda mount: json.dumps(
            mount, sort_keys=True, separators=(",", ":")))
        result[str(item["Name"])] = identity
    return result


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


def _sha256(path: Path, budget: SnapshotBudget | None = None) -> str:
    if budget is not None:
        budget.remaining_seconds()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if budget is not None:
                budget.remaining_seconds()
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(directory: Path, files: Iterable[Path], budget: SnapshotBudget | None = None) -> Path:
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
        lines.append(f"{_sha256(target, budget)}  {target.name}\n")
    manifest = directory / "SHA256SUMS"
    _write_private_text(manifest, "".join(lines))
    return manifest


def verify_manifest(directory: Path, budget: SnapshotBudget | None = None) -> None:
    """Verify every entry in a strict direct-child SHA-256 manifest."""
    directory = directory.resolve()
    manifest = directory / "SHA256SUMS"
    if manifest.is_symlink():
        raise ValueError("manifest must not be a symlink")
    lines = manifest.read_text(encoding="ascii").splitlines()
    if not lines:
        raise ValueError("manifest must not be empty")
    seen: set[str] = set()
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if match is None:
            raise ValueError("manifest contains an invalid entry")
        expected, name = match.groups()
        if name in seen or name in {".", "..", "SHA256SUMS"}:
            raise ValueError("manifest has a duplicate or invalid target")
        seen.add(name)
        target = directory / name
        if target.is_symlink():
            raise ValueError(f"manifest target is unsafe: {name}")
        target = target.resolve()
        if target.parent != directory or not target.is_file():
            raise ValueError(f"manifest target is unsafe: {name}")
        if _sha256(target, budget) != expected:
            raise ValueError(f"manifest checksum mismatch: {name}")


def _write_private_text(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_private_binary(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def _resolve_stack_file(stack_root: Path, path: Path) -> Path:
    resolved_root = stack_root.resolve()
    resolved = _safe_source_path(path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"recovery file escapes stack root: {path}") from error
    return resolved


def recovery_files(stack_root: Path) -> list[Path]:
    """Return regular, non-symlink files from the recovery allowlist."""
    stack_root = _safe_source_path(stack_root).resolve()
    files: list[Path] = []
    for relative in REQUIRED_RECOVERY_PATHS:
        target = stack_root / relative
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(f"required recovery file is unavailable: {relative}")
        files.append(_resolve_stack_file(stack_root, target))
    for relative in OPTIONAL_RECOVERY_PATHS:
        target = stack_root / relative
        _safe_source_path(target)
        if target.is_file():
            files.append(_resolve_stack_file(stack_root, target))
        elif target.is_dir():
            for child in target.rglob("*"):
                _safe_source_path(child)
                if child.is_file():
                    files.append(_resolve_stack_file(stack_root, child))
                elif not child.is_dir():
                    raise ValueError("recovery source is not a regular file or directory")
    return sorted(set(files), key=lambda path: str(path.relative_to(stack_root)))


def _write_recovery_archive(
    stack_root: Path,
    destination: Path,
    external_roots: tuple[OptionalExternalRecoveryRoot, ...],
    budget: SnapshotBudget,
    generated_sources: tuple[tuple[Path, str], ...] = (),
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    staging = destination.parent / "snapshots"
    staging.mkdir(mode=0o700)
    sources: list[tuple[Path, str]] = [
        (source, source.relative_to(stack_root).as_posix())
        for source in recovery_files(stack_root)
    ]
    sources.extend(generated_sources)
    roots_metadata: list[dict[str, object]] = []
    names: set[str] = set()
    inventories: list[tuple[OptionalExternalRecoveryRoot, list[Path]]] = []
    for root in external_roots:
        if root.name in names:
            raise ValueError("duplicate external recovery root")
        names.add(root.name)
        files = _external_files(root, budget)
        present = root.path.exists()
        attributes = root.path.stat() if present else None
        roots_metadata.append({"name": root.name, "source": str(root.path),
            "required": root.required, "status": "captured" if present else "missing",
            "required_sqlite": list(root.required_sqlite),
            "directories": _directory_recovery_metadata(root, budget),
            "discovered_files": len(files), "archive_prefix": f"external/{root.name}",
            "is_directory": root.path.is_dir() if present else None,
            "source_uid": attributes.st_uid if attributes is not None else None,
            "source_gid": attributes.st_gid if attributes is not None else None,
            "source_mode": stat.S_IMODE(attributes.st_mode) if attributes is not None else None})
        inventories.append((root, files))
        for source in files:
            relative = source.name if root.path.is_file() else source.relative_to(root.path).as_posix()
            sources.append((source, f"external/{root.name}/{relative}"))
    source_paths = {source for source, _ in sources}
    if len(sources) > MAXIMUM_SOURCE_FILES:
        raise ValueError("recovery bundle exceeds the file-count limit")
    sqlite_paths: set[Path] = set()
    for source, _ in sources:
        budget.remaining_seconds()
        if _sqlite_source(source):
            sqlite_paths.add(source)
    sidecars = {source for source, _ in sources if _sqlite_sidecar(source, sqlite_paths)}
    source_bytes = sum(_regular_stat(source).st_size for source in source_paths)
    if source_bytes > budget.maximum_bytes:
        raise ValueError("backup sources exceed configured byte limit")
    if shutil.disk_usage(destination.parent).free < budget.minimum_free_bytes + source_bytes * 2:
        raise OSError("insufficient backup space")
    entries: list[dict[str, object]] = []
    staged_sources: list[tuple[Path, Path, os.stat_result, bool]] = []
    archive_names: set[str] = set()
    try:
        for source, archive_path in sorted(sources, key=lambda item: item[1]):
            if source in sidecars:
                continue
            if archive_path in archive_names:
                raise ValueError("duplicate recovery archive member")
            archive_names.add(archive_path)
            staged = staging / str(len(entries))
            started = datetime.now().astimezone().isoformat()
            is_sqlite = source in sqlite_paths
            original = (_snapshot_sqlite(source, staged, budget) if is_sqlite
                else _copy_stable_file(source, staged, budget))
            entries.append({"archive_path": archive_path, "source": str(source),
                "source_uid": original.st_uid, "source_gid": original.st_gid,
                "source_mode": stat.S_IMODE(original.st_mode),
                "snapshot_started_at": started,
                "snapshot_finished_at": datetime.now().astimezone().isoformat(),
                "snapshot_method": "sqlite-online-backup" if is_sqlite else "stable-file-copy",
                "validation": "sqlite-quick-check-ok" if is_sqlite else "source-identity-stable",
                "bytes": staged.stat().st_size, "sha256": _sha256(staged, budget)})
            staged_sources.append((source, staged, original, is_sqlite))
        with _open_private_binary(destination) as output_handle:
            with tarfile.open(fileobj=output_handle, mode="w:gz", dereference=False) as archive:
                for entry, (_, staged, _, _) in zip(entries, staged_sources, strict=True):
                    budget.check_space(destination.parent)
                    info = tarfile.TarInfo(str(entry["archive_path"]))
                    info.size = int(entry["bytes"])
                    info.mode = 0o600
                    info.uid = info.gid = 0
                    with staged.open("rb") as input_handle:
                        archive.addfile(info, BoundedSnapshotReader(input_handle, budget, destination.parent))
            output_handle.flush()
            os.fsync(output_handle.fileno())
        for source, _, original, is_sqlite in staged_sources:
            final = _regular_stat(source)
            if is_sqlite:
                if (final.st_dev, final.st_ino) != (original.st_dev, original.st_ino):
                    raise RecoverySourceDriftError("SQLite recovery source was replaced during bundle capture", source, "bundle-verification")
            elif _source_fingerprint(final) != _source_fingerprint(original):
                raise RecoverySourceDriftError("recovery source changed before bundle verification", source, "bundle-verification")
        for root_metadata, (root, before_files) in zip(roots_metadata, inventories, strict=True):
            final_files = _external_files(root, budget)
            before_regular = {source for source in before_files if not _sqlite_sidecar(source, sqlite_paths)}
            final_regular = {source for source in final_files if not _sqlite_sidecar(source, sqlite_paths)}
            if final_regular != before_regular:
                raise RecoverySourceDriftError("recovery directory membership changed during capture", root.path, "directory-verification")
            if root_metadata["directories"] != _directory_recovery_metadata(root, budget):
                raise RecoverySourceDriftError("recovery directory permissions or membership changed during capture", root.path, "directory-verification")
        for root_metadata in roots_metadata:
            prefix = str(root_metadata["archive_prefix"]) + "/"
            root_metadata["captured_files"] = sum(str(entry["archive_path"]).startswith(prefix) for entry in entries)
            root_metadata["omitted_sqlite_sidecars"] = sum(
                source in sidecars for source, name in sources if name.startswith(prefix))
        return entries, roots_metadata
    finally:
        shutil.rmtree(staging)


def verify_bundle(directory: Path, budget: SnapshotBudget | None = None) -> None:
    """Verify manifest plus every safe archive member against captured content proof."""
    directory = _safe_source_path(directory)
    verify_manifest(directory, budget)
    required_files = {"database.pgdump", "recovery-config.tar.gz", "metadata.json", "SHA256SUMS"}
    if {item.name for item in directory.iterdir()} != required_files:
        raise ValueError("bundle contains unexpected or missing files")
    if os.name != "nt":
        if directory.stat().st_uid != 0 or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise PermissionError("recovery bundle directory must be root-only")
        for path in directory.iterdir():
            attributes = _regular_stat(path)
            if attributes.st_uid != 0 or stat.S_IMODE(attributes.st_mode) != 0o600:
                raise PermissionError("recovery bundle files must be root-only")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    entries = metadata.get("recovery_archive_entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("bundle has no recovery entry proof")
    expected: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("archive_path"), str):
            raise ValueError("invalid recovery entry proof")
        name = entry["archive_path"]
        if name in expected:
            raise ValueError("duplicate recovery entry proof")
        expected[name] = entry
    seen: set[str] = set()
    with tarfile.open(directory / "recovery-config.tar.gz", mode="r:gz") as archive:
        for member in archive:
            if budget is not None:
                budget.remaining_seconds()
            name = member.name
            if (not member.isfile() or name.startswith("/") or "\\" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in seen or name not in expected or member.mode != 0o600
                or member.uid != 0 or member.gid != 0):
                raise ValueError("unsafe or unexpected recovery archive member")
            seen.add(name)
            entry = expected[name]
            if member.size != entry.get("bytes"):
                raise ValueError("recovery archive member size mismatch")
            digest = hashlib.sha256()
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("recovery archive member cannot be read")
            with handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    if budget is not None:
                        budget.remaining_seconds()
                    digest.update(block)
            if digest.hexdigest() != entry.get("sha256"):
                raise ValueError("recovery archive content proof mismatch")
    if seen != set(expected):
        raise ValueError("recovery archive is incomplete")


def run_backup(
    *,
    stack_root: Path,
    backup_root: Path,
    retain: int,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
    external_roots: tuple[OptionalExternalRecoveryRoot, ...] = (),
    snapshot_timeout_seconds: float = 900,
    maximum_snapshot_bytes: int = DEFAULT_MAXIMUM_SNAPSHOT_BYTES,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    runtime_containers: tuple[str, ...] = (),
    database_size_bytes: int | None = None,
) -> Path:
    """Create, verify, publish, and rotate one production recovery bundle."""
    if retain < 1:
        raise ValueError("retain must be at least one")
    stack_root = _safe_source_path(stack_root).resolve()
    backup_root = _safe_source_path(backup_root)
    if backup_root == Path(backup_root.anchor) or stack_root.is_relative_to(backup_root):
        raise ValueError("backup destination must not be a filesystem root or stack ancestor")
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    backup_root = backup_root.resolve()
    if os.name != "nt" and backup_root.stat().st_uid != 0:
        raise PermissionError("backup root must be owned by root")
    budget = SnapshotBudget(snapshot_timeout_seconds, maximum_snapshot_bytes, minimum_free_bytes)
    budget.check_space(backup_root)
    if runtime_containers and (len(set(runtime_containers)) != len(runtime_containers)
        or not set(runtime_containers).issubset(PRODUCTION_RUNTIME_CONTAINERS)):
        raise ValueError("runtime descriptor targets are outside the fixed allowlist")
    if database_size_bytes is not None:
        if type(database_size_bytes) is not int or database_size_bytes <= 0:
            raise ValueError("invalid PostgreSQL size estimate")
        budget.check_space(backup_root, database_size_bytes * 2)
    for root in external_roots:
        if root.path.absolute() == Path(root.path.anchor):
            raise ValueError("filesystem root is not an authorized recovery target")
        try:
            backup_root.relative_to(root.path.absolute())
        except ValueError:
            pass
        else:
            raise ValueError("recovery target must not contain the backup root")
        _external_files(root, budget)
    timestamp = now or datetime.now().astimezone()
    suffix = timestamp.strftime("%Y%m%d-%H%M%S")
    final_directory = backup_root / f"newapi-{suffix}"
    temporary_directory = backup_root / f".newapi-{suffix}.tmp-{os.getpid()}"
    if final_directory.exists() or temporary_directory.exists():
        raise FileExistsError("backup destination already exists")
    temporary_directory.mkdir(mode=0o700)

    try:
        initial_runtime: list[dict[str, object]] = []
        generated_sources: tuple[tuple[Path, str], ...] = ()
        runtime_path = temporary_directory / "runtime-descriptors.json"
        if runtime_containers:
            initial_runtime = _capture_runtime_descriptors(runtime_containers, runtime_path, stack_root, budget, runner)
            generated_sources = ((runtime_path, "runtime/docker-containers.json"),)
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
                stderr=subprocess.DEVNULL,
                timeout=budget.remaining_seconds(),
            )
            dump_handle.flush()
            os.fsync(dump_handle.fileno())
        if not dump_path.stat().st_size or dump_path.stat().st_size > maximum_snapshot_bytes:
            raise ValueError("database dump is empty or exceeds configured byte limit")
        budget.staged_bytes += dump_path.stat().st_size
        budget.check_space(temporary_directory)
        with dump_path.open("rb") as dump_handle:
            runner(
                ["docker", "compose", "exec", "-T", "postgres", "pg_restore", "-l"],
                cwd=stack_root,
                check=True,
                stdin=dump_handle,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=budget.remaining_seconds(),
            )

        archive_path = temporary_directory / "recovery-config.tar.gz"
        archive_entries, external_metadata = _write_recovery_archive(
            stack_root, archive_path, external_roots, budget, generated_sources)
        if runtime_containers:
            final_runtime_path = temporary_directory / "runtime-descriptors-final.json"
            final_runtime = _capture_runtime_descriptors(runtime_containers, final_runtime_path, stack_root, budget, runner)
            if _runtime_recovery_identity(initial_runtime) != _runtime_recovery_identity(final_runtime):
                raise RuntimeError("runtime recovery configuration changed during capture")
            runtime_path.unlink()
            final_runtime_path.unlink()
        archived_files = [str(entry["archive_path"]) for entry in archive_entries]
        metadata_path = temporary_directory / "metadata.json"
        metadata = {
            "created_at": timestamp.isoformat(),
            "database_format": "postgres-custom",
            "database_bytes": dump_path.stat().st_size,
            "database_size_estimate_bytes": database_size_bytes,
            "runtime_descriptors": "captured-and-rechecked" if runtime_containers else "not-configured",
            "runtime_container_count": len(runtime_containers),
            "recovery_archive_files": archived_files,
            "recovery_archive_entries": archive_entries,
            "external_recovery_roots": external_metadata,
            "coverage_status": ("core_only" if not external_roots else "partial"
                if any(root["status"] == "missing" for root in external_metadata) else "configured_complete"),
            "consistency": "per-store snapshots; no coordinated cross-store point-in-time guarantee",
            "credential_exposure": "root-only local bundle; never publish via a public HTTP directory",
            "retention_count": retain,
        }
        _write_private_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        write_manifest(
            temporary_directory,
            [dump_path, archive_path, metadata_path],
            budget,
        )
        verify_bundle(temporary_directory, budget)
        budget.check_space(temporary_directory)
        for child in temporary_directory.iterdir():
            os.chmod(child, 0o600)
        _fsync_directory(temporary_directory)
        os.replace(temporary_directory, final_directory)
        os.chmod(final_directory, 0o700)
        _fsync_directory(backup_root)
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
    parser.add_argument("--external-config", type=Path)
    parser.add_argument("--snapshot-timeout-seconds", type=float, default=900)
    parser.add_argument("--maximum-snapshot-bytes", type=int, default=DEFAULT_MAXIMUM_SNAPSHOT_BYTES)
    parser.add_argument("--minimum-free-bytes", type=int, default=DEFAULT_MINIMUM_FREE_BYTES)
    args = parser.parse_args()
    if os.name != "nt" and os.geteuid() != 0:
        raise PermissionError("production backup worker must run as root")
    configuration = args.external_config or args.stack_root / "channel-monitor/config/production-backup-roots.json"
    _safe_source_path(configuration)
    roots, runtime_names = load_recovery_scope(configuration) if configuration.exists() else ((), PRODUCTION_RUNTIME_CONTAINERS)
    if args.external_config is not None and not configuration.exists():
        raise FileNotFoundError("explicit recovery target configuration is unavailable")
    result = run_backup(
        stack_root=args.stack_root,
        backup_root=args.backup_root,
        retain=args.retain,
        external_roots=roots,
        snapshot_timeout_seconds=args.snapshot_timeout_seconds,
        maximum_snapshot_bytes=args.maximum_snapshot_bytes,
        minimum_free_bytes=args.minimum_free_bytes,
        runtime_containers=runtime_names,
        database_size_bytes=postgresql_database_size(args.stack_root),
    )
    print(f"backup_complete={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
