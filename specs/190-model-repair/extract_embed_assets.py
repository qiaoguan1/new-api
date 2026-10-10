"""Recover only the exact public frontend assets from an immutable Go ELF.

This offline tool does not execute the input, read credentials, rebuild a UI, or
contact a service. It accepts only ELF64 little-endian amd64 ET_EXEC built with
the independently verified go1.26.1 compiler. Both complete embed.FS tables and
an independently fetched default JS entry SHA256 must be verified before output
is created. Copy the recovered web/*/dist trees into the *exact* release source;
the manifest is evidence, not a claim that the backend source is identical.
The original production build's exact 145-byte classic placeholder is allowed
without JS only under its attested SHA256 profile, and is labelled as such. This
does not rebuild the classic UI or accept arbitrary no-JS HTML.

Primary layout/hash sources checked on 2026-10-10:
https://github.com/golang/go/blob/go1.26.1/src/cmd/compile/internal/staticdata/embed.go
https://github.com/golang/go/blob/go1.26.1/src/cmd/compile/internal/staticdata/data.go
https://github.com/golang/go/blob/go1.26.1/src/cmd/internal/hash/hash.go
https://github.com/golang/go/blob/go1.26.1/src/debug/buildinfo/buildinfo.go

On amd64, WriteEmbed emits a 24-byte self-referencing slice header followed by
48-byte {name string, data string, hash [16]byte} records. The compiler hash is
NOT plain SHA256: <=1024 bytes uses SHA256(data) with byte 0 XOR 0xff; larger
files use SHA256(0x01 || data). Both hashes are truncated to the first 16 bytes.

Example (hashes supplied from independently attested binary/public HTTP bytes):
  python3 extract_embed_assets.py --binary native-live.binary --output assets \
    --expected-binary-sha256 SHA256 --expected-go-version go1.26.1 \
    --public-asset-sha256 web/default/dist/assets/index-HASH.js=SHA256
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import math
import mmap
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import sys
import time
from typing import Mapping
from urllib.parse import unquote, urlsplit


ROOTS = ("web/default/dist", "web/classic/dist")
GO_VERSION = "go1.26.1"
BUILD_MAGIC = b"\xff Go buildinf:"
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
RECORD_SIZE = 48
SLICE_SIZE = 24
CLASSIC_PLACEHOLDER_BYTES = 145
CLASSIC_PLACEHOLDER_SHA256 = "8b37d69ae84c42475927c07c5d63cf9a70472f35069c98179988320a051b3633"


class ExtractionError(RuntimeError):
    """A sanitized verification failure; no existing file is overwritten."""


@dataclass(frozen=True)
class Limits:
    """Hard resource bounds for untrusted ELF parsing and owned output."""

    max_binary_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_records: int = 20000
    max_tables: int = 8
    max_name_bytes: int = 1024
    max_seconds: float = 120.0


@dataclass(frozen=True)
class Segment:
    """One non-overlapping, file-backed ELF PT_LOAD mapping."""

    offset: int
    address: int
    size: int
    flags: int


@dataclass(frozen=True)
class EmbeddedFile:
    """Verified public file bytes with its source record provenance."""

    path: str
    data: bytes
    record_offset: int
    table_offset: int


def _compiler_hash(data: bytes | memoryview) -> bytes:
    if len(data) <= 1024:
        digest = bytearray(hashlib.sha256(data).digest())
        digest[0] ^= 0xFF
        return bytes(digest[:16])
    digest = hashlib.sha256(b"\x01")
    digest.update(data)
    return digest.digest()[:16]


def _safe_path(name: str, *, directory: bool = False) -> str:
    """Reject traversal, ambiguous portable names, and non-public file scopes."""
    if directory:
        if not name.endswith("/"):
            raise ExtractionError("directory_name_invalid")
        name = name[:-1]
    if not name or name.startswith("/") or name.endswith("/"):
        raise ExtractionError("asset_path_invalid")
    parts = name.split("/")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((".", " ")):
            raise ExtractionError("asset_path_invalid")
        if any(ord(char) < 32 or ord(char) == 127 or char in '\\:*?"<>|`\'' for char in part):
            raise ExtractionError("asset_path_invalid")
        if part.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }:
            raise ExtractionError("asset_path_invalid")
    inside = any(name == root or name.startswith(root + "/") for root in ROOTS)
    ancestor = directory and any(root.startswith(name + "/") for root in ROOTS)
    if not inside and not ancestor:
        raise ExtractionError("non_frontend_record_refused")
    return name


class ELFImage:
    """Read-only address translation, compiler identity, and complete FS proof."""

    def __init__(self, image: mmap.mmap, limits: Limits, deadline: float):
        self.image = image
        self.limits = limits
        self.deadline = deadline
        self.segments: list[Segment] = []
        if len(image) < 64:
            raise ExtractionError("elf_header_truncated")
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", image, 0)
        ident, kind, machine, version = header[:4]
        if ident[:4] != b"\x7fELF" or ident[4:7] != b"\x02\x01\x01":
            raise ExtractionError("unsupported_elf_format")
        if kind != 2 or machine != 62 or version != 1 or header[8] != 64:
            raise ExtractionError("unsupported_elf_machine_or_type")
        ph_offset, ph_size, ph_count = header[5], header[9], header[10]
        if ph_size != 56 or not 1 <= ph_count <= 64 or ph_offset + ph_count * 56 > len(image):
            raise ExtractionError("elf_program_headers_invalid")
        for index in range(ph_count):
            kind, flags, offset, address, _, file_size, memory_size, _ = struct.unpack_from(
                "<IIQQQQQQ", image, ph_offset + index * 56
            )
            if kind != 1 or file_size == 0:
                continue
            if file_size > memory_size or offset + file_size > len(image) or address + memory_size >= 1 << 64:
                raise ExtractionError("elf_load_mapping_invalid")
            if not flags & 4:
                raise ExtractionError("elf_load_not_readable")
            self.segments.append(Segment(offset, address, file_size, flags))
        if not self.segments:
            raise ExtractionError("elf_load_mapping_missing")
        for index, left in enumerate(self.segments):
            for right in self.segments[index + 1 :]:
                if max(left.address, right.address) < min(left.address + left.size, right.address + right.size):
                    raise ExtractionError("elf_address_mapping_ambiguous")
                if max(left.offset, right.offset) < min(left.offset + left.size, right.offset + right.size):
                    raise ExtractionError("elf_file_mapping_ambiguous")

    def check_deadline(self) -> None:
        """Refuse expensive/corrupt input before the bounded operation expires."""
        if time.monotonic() > self.deadline:
            raise ExtractionError("verification_deadline_exceeded")

    def offset_for(self, address: int, size: int) -> int:
        """Map only a complete file-backed range, never BSS or padding."""
        if size < 0 or address + size >= 1 << 64:
            raise ExtractionError("elf_data_range_invalid")
        for segment in self.segments:
            if segment.address <= address and address + size <= segment.address + segment.size:
                return segment.offset + address - segment.address
        raise ExtractionError("elf_data_range_unmapped")

    def read(self, address: int, size: int) -> bytes:
        """Return a verified file-backed byte range; zero empty pointers allowed."""
        if address == 0 and size == 0:
            return b""
        offset = self.offset_for(address, size)
        return self.image[offset : offset + size]

    def name_at(self, record_offset: int) -> str:
        """Read only an embed record name, with bounded UTF-8 validation."""
        if record_offset + 16 > len(self.image):
            raise ExtractionError("embed_record_truncated")
        pointer, length = struct.unpack_from("<QQ", self.image, record_offset)
        if not 1 <= length <= self.limits.max_name_bytes:
            raise ExtractionError("embed_name_length_invalid")
        try:
            return self.read(pointer, length).decode("utf-8")
        except UnicodeError:
            raise ExtractionError("embed_name_encoding_invalid") from None

    def go_version(self) -> str:
        """Read only compiler-version bytes from an inline Go build-info blob."""
        matches: list[str] = []
        for segment in self.segments:
            if segment.flags & 3 != 2:
                continue
            position = segment.offset
            end = segment.offset + segment.size
            while True:
                position = self.image.find(BUILD_MAGIC, position, end)
                if position < 0:
                    break
                address = segment.address + position - segment.offset
                if address % 16 == 0:
                    if position + 32 >= end or self.image[position + 14 : position + 16] != b"\x08\x02":
                        raise ExtractionError("go_build_info_invalid")
                    start = position + 32
                    length = self.image[start]
                    if not 1 <= length <= 64 or start + 1 + length > end:
                        raise ExtractionError("go_build_version_invalid")
                    try:
                        matches.append(self.image[start + 1 : start + 1 + length].decode("ascii"))
                    except UnicodeError:
                        raise ExtractionError("go_build_version_invalid") from None
                position += len(BUILD_MAGIC)
        if len(matches) != 1:
            raise ExtractionError("go_build_info_missing_or_ambiguous")
        return matches[0]

    def _fs_owner_exists(self, header_address: int) -> bool:
        needle = struct.pack("<Q", header_address)
        for segment in self.segments:
            if not segment.flags & 2 or segment.flags & 1:
                continue
            position = segment.offset
            end = position + segment.size
            while True:
                position = self.image.find(needle, position, end)
                if position < 0:
                    break
                if (segment.address + position - segment.offset) % 8 == 0:
                    return True
                position += 1
        return False

    def _file_record(self, offset: int, table_offset: int) -> EmbeddedFile | None:
        name = self.name_at(offset)
        directory = name.endswith("/")
        clean = _safe_path(name, directory=directory)
        _, _, pointer, length = struct.unpack_from("<QQQQ", self.image, offset)
        checksum = self.image[offset + 32 : offset + 48]
        if directory:
            if pointer or length or checksum != b"\x00" * 16:
                raise ExtractionError("embed_directory_record_invalid")
            return None
        if length > self.limits.max_file_bytes:
            raise ExtractionError("asset_file_limit_exceeded")
        data = self.read(pointer, length)
        if checksum != _compiler_hash(data):
            raise ExtractionError("embed_file_hash_mismatch")
        return EmbeddedFile(clean, data, offset, table_offset)

    def embedded_frontends(self) -> tuple[list[EmbeddedFile], list[dict[str, object]]]:
        """Verify entire compiler-declared tables, their owners, and orphan records."""
        if sys.byteorder != "little":
            raise ExtractionError("unsupported_host_byte_order")
        table_headers: list[tuple[int, int, int]] = []
        target_records: set[int] = set()
        readonly = [segment for segment in self.segments if segment.flags == 4]
        for segment in readonly:
            self.check_deadline()
            aligned = (-segment.address) % 8
            start = segment.offset + aligned
            end = segment.offset + segment.size
            end -= (end - start) % 8
            with memoryview(self.image)[start:end].cast("Q") as words:
                for index, pointer in enumerate(words):
                    if index % 16384 == 0:
                        self.check_deadline()
                    position = start + index * 8
                    address = segment.address + position - segment.offset
                    if pointer == address + SLICE_SIZE and position + SLICE_SIZE + RECORD_SIZE <= end:
                        try:
                            first_name = self.name_at(position + SLICE_SIZE)
                        except ExtractionError:
                            first_name = ""
                        if first_name == "web/":
                            length, capacity = struct.unpack_from("<QQ", self.image, position + 8)
                            if length != capacity or not 1 <= length <= self.limits.max_records:
                                raise ExtractionError("embed_slice_length_invalid")
                            table_headers.append((position, address, length))
                            if len(table_headers) > self.limits.max_tables:
                                raise ExtractionError("embed_table_limit_exceeded")
                    if position + RECORD_SIZE > end or index + 1 >= len(words):
                        continue
                    name_length = words[index + 1]
                    if not 1 <= name_length <= len(self.image):
                        continue
                    for data_segment in readonly:
                        if not data_segment.address <= pointer < data_segment.address + data_segment.size:
                            continue
                        name_offset = data_segment.offset + pointer - data_segment.address
                        if any(name_length >= len(root) + 1 and
                               self.image[name_offset : name_offset + len(root) + 1] == (root + "/").encode()
                               for root in ROOTS):
                            target_records.add(position)
                            if len(target_records) > self.limits.max_records:
                                raise ExtractionError("embed_record_limit_exceeded")
                        break
        if not table_headers:
            raise ExtractionError("complete_embed_tables_not_found")
        files: list[EmbeddedFile] = []
        table_proofs: list[dict[str, object]] = []
        covered: set[int] = set()
        all_paths: set[str] = set()
        portable_paths: dict[str, str] = {}
        total_bytes = 0
        total_records = 0
        for offset, address, count in table_headers:
            self.check_deadline()
            span = SLICE_SIZE + count * RECORD_SIZE
            if self.offset_for(address, span) != offset or not self._fs_owner_exists(address):
                raise ExtractionError("embed_fs_owner_missing")
            total_records += count
            if total_records > self.limits.max_records:
                raise ExtractionError("embed_record_limit_exceeded")
            previous: tuple[str, str] | None = None
            directory_names: set[str] = set()
            table_files: list[EmbeddedFile] = []
            for index in range(count):
                record = offset + SLICE_SIZE + index * RECORD_SIZE
                name = self.name_at(record)
                trimmed = name.rstrip("/")
                previous_spelling = portable_paths.setdefault(trimmed.casefold(), trimmed)
                if previous_spelling != trimmed:
                    raise ExtractionError("asset_path_case_alias_refused")
                directory, _, base = trimmed.rpartition("/")
                ordering = (directory or ".", base)
                if previous is not None and ordering <= previous:
                    raise ExtractionError("embed_record_order_or_duplicate_invalid")
                previous = ordering
                item = self._file_record(record, offset)
                covered.add(record)
                if item is None:
                    directory_names.add(_safe_path(name, directory=True))
                    continue
                portable_name = item.path.casefold()
                if portable_name in all_paths:
                    raise ExtractionError("asset_duplicate_or_ambiguous")
                all_paths.add(portable_name)
                total_bytes += len(item.data)
                if total_bytes > self.limits.max_total_bytes:
                    raise ExtractionError("asset_total_limit_exceeded")
                table_files.append(item)
            required_directories: set[str] = set()
            for item in table_files:
                parent = PurePosixPath(item.path).parent
                while parent.as_posix() != ".":
                    required_directories.add(parent.as_posix())
                    parent = parent.parent
            if directory_names != required_directories:
                raise ExtractionError("embed_directory_closure_incomplete")
            files.extend(table_files)
            table_proofs.append({
                "offset": offset, "record_count": count, "file_count": len(table_files),
                "directory_count": len(directory_names),
                "table_sha256": hashlib.sha256(self.image[offset : offset + span]).hexdigest(),
            })
        for record in target_records - covered:
            self.check_deadline()
            # A genuine orphan must never be hidden by copy-size/path limits.
            # Verify its shape/hash via a bounded ELF memoryview, without copying
            # bytes or treating every bare Go string header as a file record.
            try:
                name_pointer, name_length, pointer, length = struct.unpack_from("<QQQQ", self.image, record)
                name_offset = self.offset_for(name_pointer, name_length)
                checksum = self.image[record + 32 : record + 48]
                if self.image[name_offset + name_length - 1] == ord("/"):
                    if pointer or length or checksum != b"\x00" * 16:
                        continue
                else:
                    if pointer == 0 and length == 0:
                        if checksum != _compiler_hash(b""):
                            continue
                    else:
                        offset = self.offset_for(pointer, length)
                        with memoryview(self.image)[offset : offset + length] as content:
                            if checksum != _compiler_hash(content):
                                continue
            except ExtractionError:
                continue  # A bare Go string header is not an embed.file record.
            raise ExtractionError("orphan_frontend_record_refused")
        if not files:
            raise ExtractionError("frontend_files_missing")
        return files, table_proofs


class EntryReferences(HTMLParser):
    """Extract only local script, stylesheet, and module-preload entry URLs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.urls.append(str(values["src"]))
        elif tag == "link" and str(values.get("rel", "")).lower() in ("stylesheet", "modulepreload") and values.get("href"):
            self.urls.append(str(values["href"]))
        elif tag == "base":
            raise ExtractionError("html_base_reference_refused")


def _entry_assets(
    files: list[EmbeddedFile], independent: Mapping[str, str]
) -> tuple[dict[str, list[str]], dict[str, dict[str, object]]]:
    """Verify local entries, allowing only the exact original classic placeholder."""
    by_name = {item.path: item for item in files}
    entries: dict[str, list[str]] = {}
    profiles: dict[str, dict[str, object]] = {}
    for root in ROOTS:
        index = by_name.get(root + "/index.html")
        if index is None:
            raise ExtractionError("frontend_index_missing")
        parser = EntryReferences()
        try:
            parser.feed(index.data.decode("utf-8"))
            parser.close()
        except UnicodeError:
            raise ExtractionError("frontend_index_encoding_invalid") from None
        names: set[str] = set()
        for url in parser.urls:
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise ExtractionError("external_or_fragment_entry_refused")
            path = unquote(parsed.path)
            if path.startswith("/"):
                path = path[1:]
            elif path.startswith("./"):
                path = path[2:]
            target = _safe_path(root + "/" + path)
            if not target.startswith(root + "/") or target not in by_name:
                raise ExtractionError("frontend_entry_asset_missing")
            names.add(target)
        index_sha256 = hashlib.sha256(index.data).hexdigest()
        profile = "local_js_entry_assets"
        if not any(name.endswith(".js") for name in names):
            if root != ROOTS[1] or parser.urls or len(index.data) != CLASSIC_PLACEHOLDER_BYTES or \
               index_sha256 != CLASSIC_PLACEHOLDER_SHA256:
                raise ExtractionError("frontend_js_entry_missing")
            profile = "attested_original_build_placeholder_no_js"
        entries[root] = sorted(names)
        profiles[root] = {"profile": profile, "index_bytes": len(index.data), "index_sha256": index_sha256}
    if not any(name in entries[ROOTS[0]] and name.endswith(".js") for name in independent):
        raise ExtractionError("independent_default_js_hash_required")
    for name, checksum in independent.items():
        _safe_path(name)
        if not HASH_PATTERN.fullmatch(checksum) or name not in by_name:
            raise ExtractionError("independent_asset_proof_invalid")
        if hashlib.sha256(by_name[name].data).hexdigest() != checksum:
            raise ExtractionError("independent_asset_hash_mismatch")
    return entries, profiles


def extract_assets(
    binary: Path,
    output: Path,
    *,
    expected_binary_sha256: str,
    expected_go_version: str,
    public_asset_sha256: Mapping[str, str],
    limits: Limits = Limits(),
) -> dict[str, object]:
    """Verify both complete public trees and then create a new private output.

    Refusals happen before writes. File I/O failures after exclusive directory
    creation remove only this operation's newly created directory. Existing
    paths, links, and sources are never changed. No ELF bytes are executed.
    """
    if not HASH_PATTERN.fullmatch(expected_binary_sha256) or expected_go_version != GO_VERSION:
        raise ExtractionError("expected_binary_or_compiler_identity_invalid")
    maximum = Limits()
    dimensions = ("max_binary_bytes", "max_file_bytes", "max_total_bytes", "max_records", "max_tables", "max_name_bytes")
    if any(not isinstance(getattr(limits, name), int) or isinstance(getattr(limits, name), bool) or
           not 0 < getattr(limits, name) <= getattr(maximum, name) for name in dimensions):
        raise ExtractionError("resource_limits_invalid")
    if not isinstance(limits.max_seconds, (int, float)) or isinstance(limits.max_seconds, bool) or \
       not math.isfinite(limits.max_seconds) or not 0 < limits.max_seconds <= maximum.max_seconds:
        raise ExtractionError("resource_limits_invalid")
    binary, output = Path(binary).absolute(), Path(output).absolute()
    if ".." in output.parts or output.exists() or output.is_symlink():
        raise ExtractionError("output_must_be_a_new_directory")
    parent = output.parent
    if not parent.is_dir() or any(part.is_symlink() for part in (parent, *parent.parents)):
        raise ExtractionError("output_parent_invalid")
    source_stat = binary.lstat()
    if not stat.S_ISREG(source_stat.st_mode) or not 64 <= source_stat.st_size <= limits.max_binary_bytes:
        raise ExtractionError("binary_source_invalid_or_oversized")
    deadline = time.monotonic() + limits.max_seconds
    with binary.open("rb") as source:
        opened_stat = os.fstat(source.fileno())
        if (opened_stat.st_dev, opened_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino):
            raise ExtractionError("binary_source_changed")
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            if hashlib.sha256(mapped).hexdigest() != expected_binary_sha256:
                raise ExtractionError("binary_source_hash_mismatch")
            image = ELFImage(mapped, limits, deadline)
            if image.go_version() != expected_go_version:
                raise ExtractionError("binary_compiler_version_mismatch")
            files, tables = image.embedded_frontends()
            entries, frontend_profiles = _entry_assets(files, public_asset_sha256)
            image.check_deadline()
            if hashlib.sha256(mapped).hexdigest() != expected_binary_sha256:
                raise ExtractionError("binary_source_changed")
        final_stat = binary.lstat()
        if (final_stat.st_dev, final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns) != (
            source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns
        ):
            raise ExtractionError("binary_source_changed")
    manifest: dict[str, object] = {
        "schema": "xtai-exact-embedded-frontends-v1",
        "status": "verified_complete_embedded_frontends",
        "binary_sha256": expected_binary_sha256,
        "go_version": expected_go_version,
        "compiler_hash_profile": "go1.26.1-staticdata-Sum32-small-New32-large-truncated16",
        "file_count": len(files), "total_bytes": sum(len(item.data) for item in files),
        "tables": tables, "entry_assets": entries, "frontend_profiles": frontend_profiles,
        "independent_public_hashes": dict(sorted(public_asset_sha256.items())),
        "files": [
            {"path": item.path, "bytes": len(item.data), "sha256": hashlib.sha256(item.data).hexdigest(),
             "record_offset": item.record_offset, "table_offset": item.table_offset}
            for item in sorted(files, key=lambda item: item.path)
        ],
    }
    encoded = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if shutil.disk_usage(parent).free < int(manifest["total_bytes"]) + len(encoded) + 64 * 1024 * 1024:
        raise ExtractionError("output_free_space_insufficient")
    os.mkdir(output, 0o700)
    try:
        os.chmod(output, 0o700)
        for item in files:
            if time.monotonic() > deadline:
                raise ExtractionError("extraction_deadline_exceeded")
            destination = output.joinpath(*item.path.split("/"))
            relative_parents = list(destination.parent.relative_to(output).parents)
            for directory in reversed(relative_parents):
                if directory.as_posix() != ".":
                    (output / directory).mkdir(mode=0o700, exist_ok=True)
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(destination, flags, 0o644)
            try:
                asset_stream = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with asset_stream as asset:
                asset.write(item.data)
            os.chmod(destination, 0o644)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != hashlib.sha256(item.data).hexdigest():
                raise ExtractionError("written_asset_hash_mismatch")
        for name, content in (
            ("manifest.json", encoded),
            ("manifest.sha256", (hashlib.sha256(encoded).hexdigest() + "  manifest.json\n").encode("ascii")),
        ):
            descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                evidence_stream = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with evidence_stream as evidence:
                evidence.write(content)
            os.chmod(output / name, 0o600)
    except BaseException:
        shutil.rmtree(output)
        raise
    return manifest


def main() -> int:
    """Run a bounded offline extraction, returning sanitized JSON only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-binary-sha256", required=True)
    parser.add_argument("--expected-go-version", required=True)
    parser.add_argument("--public-asset-sha256", action="append", required=True, metavar="PATH=SHA256")
    args = parser.parse_args()
    try:
        independent: dict[str, str] = {}
        for item in args.public_asset_sha256:
            name, separator, checksum = item.partition("=")
            if not separator or name in independent:
                raise ExtractionError("independent_asset_proof_invalid")
            independent[name] = checksum
        manifest = extract_assets(
            args.binary, args.output, expected_binary_sha256=args.expected_binary_sha256,
            expected_go_version=args.expected_go_version, public_asset_sha256=independent,
        )
        print(json.dumps({"status": manifest["status"], "files": manifest["file_count"],
                          "bytes": manifest["total_bytes"], "tables": len(manifest["tables"]),
                          "manifest_sha256": hashlib.sha256((args.output / "manifest.json").read_bytes()).hexdigest()}))
        return 0
    except (ExtractionError, OSError, ValueError, struct.error) as error:
        reason = str(error) if isinstance(error, ExtractionError) else "offline_extraction_io_or_format_error"
        print(json.dumps({"status": "refused", "reason": reason}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
