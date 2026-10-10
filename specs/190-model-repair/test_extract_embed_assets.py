"""Contract tests for offline, exact Go embed.FS frontend recovery."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("extract_embed_assets.py")
SPEC = importlib.util.spec_from_file_location("extract_embed_assets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
extractor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = extractor
SPEC.loader.exec_module(extractor)


def compiler_hash(data: bytes) -> bytes:
    """Model the verified go1.26.1 compiler, not extractor internals."""
    if len(data) <= 1024:
        digest = bytearray(hashlib.sha256(data).digest())
        digest[0] ^= 0xFF
        return bytes(digest[:16])
    return hashlib.sha256(b"\x01" + data).digest()[:16]


def fixture_files() -> dict[str, bytes]:
    """Return both separately served frontend trees with real entry references."""
    result: dict[str, bytes] = {}
    for frontend in ("default", "classic"):
        prefix = f"web/{frontend}/dist/"
        result[prefix + "index.html"] = (
            b'<!doctype html><html><head><link rel="stylesheet" '
            b'href="/assets/main.css"></head><body><script type="module" '
            b'src="/assets/main.js"></script></body></html>'
        )
        result[prefix + "assets/main.js"] = (
            f'window.frontend="{frontend}";\n'.encode() + b"/* asset */\n" * 120
        )
        result[prefix + "assets/main.css"] = b"body{color:green}"
        result[prefix + "assets/empty.txt"] = b""
    return result


def rspack_fixture_files() -> dict[str, bytes]:
    """Model production-shaped Rspack HTML, with five hashed static JS chunks."""
    names = (
        "vendor-ui-primitives.36932e36fe.js",
        "vendor-tanstack.067f3f7ea6.js",
        "lib-react.5332daabae.js",
        "5294.6e2b5d9a71.js",
        "index.a935d36b34.js",
    )
    files: dict[str, bytes] = {}
    for frontend in ("default", "classic"):
        root = f"web/{frontend}/dist/"
        files[root + "index.html"] = (
            '<!doctype html><html><head><link rel="stylesheet" href="/static/css/index.abc.css">'
            + "".join(f'<script defer src="/static/js/{name}"></script>' for name in names)
            + "</head><body><div id=\"root\"></div></body></html>"
        ).encode()
        for name in names:
            files[root + "static/js/" + name] = f'console.log("{frontend}/{name}");'.encode()
        files[root + "static/css/index.abc.css"] = b"body{color:black}"
    return files


CLASSIC_BUILD_PLACEHOLDER = (
    b"<!DOCTYPE html><html><head><title>New API</title></head><body><h1>Classic UI</h1>"
    b"<p>Classic UI is not available in this build.</p></body></html>\n"
)


def rspack_placeholder_fixture_files() -> dict[str, bytes]:
    """Keep the independently attested 145-byte classic build placeholder exact."""
    files = {name: data for name, data in rspack_fixture_files().items() if name.startswith("web/default/")}
    files["web/classic/dist/index.html"] = CLASSIC_BUILD_PLACEHOLDER
    return files


def elf_fixture(
    files: dict[str, bytes],
    *,
    version: str = "go1.26.1",
    split_tables: bool = True,
) -> tuple[bytearray, dict[str, int], list[int]]:
    """Construct a stripped ELF64 with actual Go staticdata table layout."""
    ro_file, ro_addr = 0x1000, 0x500000
    rw_file, rw_addr = 0x10000, 0x600000
    ro = bytearray()
    records: dict[str, int] = {}
    headers: list[int] = []

    def store(data: bytes, alignment: int = 1) -> int:
        ro.extend(b"\x00" * (-len(ro) % alignment))
        address = ro_addr + len(ro)
        ro.extend(data)
        return address

    groups = [files]
    if split_tables:
        groups = [
            {name: data for name, data in files.items() if name.startswith(f"web/{kind}/")}
            for kind in ("default", "classic")
        ]
    fs_pointers = bytearray()
    for group in groups:
        if not group:
            continue
        names = dict(group)
        for name in list(group):
            parent = Path(name).parent.as_posix()
            while parent != ".":
                names.setdefault(parent + "/", b"")
                parent = Path(parent).parent.as_posix()
        ordered = sorted(names, key=lambda name: tuple(name.rstrip("/").rsplit("/", 1)))
        encoded: list[tuple[str, int, int, int]] = []
        for name in ordered:
            name_ptr = store(name.encode())
            data_ptr = 0 if name.endswith("/") else store(names[name])
            encoded.append((name, name_ptr, data_ptr, len(names[name])))
        header_ptr = store(b"\x00" * (24 + 48 * len(encoded)), 8)
        header_off = header_ptr - ro_addr
        struct.pack_into("<QQQ", ro, header_off, header_ptr + 24, len(encoded), len(encoded))
        headers.append(ro_file + header_off)
        for index, (name, name_ptr, data_ptr, length) in enumerate(encoded):
            record_off = header_off + 24 + 48 * index
            struct.pack_into("<QQQQ", ro, record_off, name_ptr, len(name), data_ptr, length)
            ro[record_off + 32 : record_off + 48] = (
                b"\x00" * 16 if name.endswith("/") else compiler_hash(names[name])
            )
            records[name] = ro_file + record_off
        fs_pointers.extend(struct.pack("<Q", header_ptr))
    rw = bytearray(fs_pointers)
    rw.extend(b"\x00" * (-len(rw) % 16))
    version_bytes = version.encode()
    rw.extend(b"\xff Go buildinf:" + bytes([8, 2]) + b"\x00" * 16)
    rw.extend(bytes([len(version_bytes)]) + version_bytes + b"\x00")
    result = bytearray(rw_file + len(rw))
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    result[:64] = struct.pack(
        "<16sHHIQQQIHHHHHH", ident, 2, 62, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0
    )
    struct.pack_into("<IIQQQQQQ", result, 64, 1, 4, ro_file, ro_addr, ro_addr, len(ro), len(ro), 0x1000)
    struct.pack_into("<IIQQQQQQ", result, 120, 1, 6, rw_file, rw_addr, rw_addr, len(rw), len(rw), 0x1000)
    result[ro_file : ro_file + len(ro)] = ro
    result[rw_file : rw_file + len(rw)] = rw
    return result, records, headers


class ExtractAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.files = fixture_files()
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        self.binary = self.root / "native.binary"
        self.output = self.root / "owned-assets"

    def run_extract(self, **overrides: object) -> dict[str, object]:
        self.binary.write_bytes(self.binary_bytes)
        kwargs: dict[str, object] = {
            "expected_binary_sha256": hashlib.sha256(self.binary_bytes).hexdigest(),
            "expected_go_version": "go1.26.1",
            "public_asset_sha256": {
                "web/default/dist/assets/main.js": hashlib.sha256(
                    self.files["web/default/dist/assets/main.js"]
                ).hexdigest()
            } if "web/default/dist/assets/main.js" in self.files else {},
        }
        kwargs.update(overrides)
        return extractor.extract_assets(self.binary, self.output, **kwargs)

    def assert_refused(self, **kwargs: object) -> None:
        with self.assertRaises(extractor.ExtractionError):
            self.run_extract(**kwargs)
        self.assertFalse(self.output.exists(), "refusal must happen before creating output")

    def test_recovers_complete_separate_tables_and_verifies_manifest(self) -> None:
        manifest = self.run_extract()
        self.assertEqual(manifest["status"], "verified_complete_embedded_frontends")
        self.assertEqual(manifest["go_version"], "go1.26.1")
        self.assertEqual(len(manifest["tables"]), 2)
        self.assertEqual(manifest["file_count"], len(self.files))
        self.assertEqual(manifest["total_bytes"], sum(map(len, self.files.values())))
        for name, data in self.files.items():
            self.assertEqual((self.output / name).read_bytes(), data)
        stored = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(stored, manifest)
        detached = (self.output / "manifest.sha256").read_text().split()[0]
        self.assertEqual(detached, hashlib.sha256((self.output / "manifest.json").read_bytes()).hexdigest())
        for record in manifest["files"]:
            actual = (self.output / record["path"]).read_bytes()
            self.assertEqual(record["sha256"], hashlib.sha256(actual).hexdigest())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((self.output / "manifest.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((self.output / "web/default/dist/index.html").stat().st_mode), 0o644)

    def test_supports_a_single_fs_containing_both_complete_frontends(self) -> None:
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files, split_tables=False)
        manifest = self.run_extract()
        self.assertEqual(len(manifest["tables"]), 1)
        self.assertEqual(manifest["file_count"], 8)

    def test_recovers_rspack_static_js_with_independent_main_entry_hash(self) -> None:
        self.files = rspack_fixture_files()
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        main = "web/default/dist/static/js/index.a935d36b34.js"
        manifest = self.run_extract(public_asset_sha256={main: hashlib.sha256(self.files[main]).hexdigest()})
        self.assertEqual(manifest["file_count"], len(self.files))
        self.assertIn(main, manifest["entry_assets"]["web/default/dist"])
        self.assertEqual(len(manifest["entry_assets"]["web/default/dist"]), 6)

    def test_rspack_still_rejects_wrong_http_main_hash_and_nonlocal_script(self) -> None:
        self.files = rspack_fixture_files()
        main = "web/default/dist/static/js/index.a935d36b34.js"
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        self.assert_refused(public_asset_sha256={main: "0" * 64})
        self.files["web/default/dist/index.html"] = self.files["web/default/dist/index.html"].replace(
            b'/static/js/index.a935d36b34.js', b'https://example.invalid/static/js/index.a935d36b34.js'
        )
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        self.assert_refused(public_asset_sha256={main: hashlib.sha256(self.files[main]).hexdigest()})

    def test_accepts_only_attested_classic_placeholder_preserving_exact_bytes(self) -> None:
        self.files = rspack_placeholder_fixture_files()
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        main = "web/default/dist/static/js/index.a935d36b34.js"
        manifest = self.run_extract(public_asset_sha256={main: hashlib.sha256(self.files[main]).hexdigest()})
        classic = "web/classic/dist"
        self.assertEqual(manifest["entry_assets"][classic], [])
        self.assertEqual(manifest["frontend_profiles"][classic], {
            "profile": "attested_original_build_placeholder_no_js",
            "index_bytes": 145,
            "index_sha256": "8b37d69ae84c42475927c07c5d63cf9a70472f35069c98179988320a051b3633",
        })
        self.assertEqual((self.output / classic / "index.html").read_bytes(), CLASSIC_BUILD_PLACEHOLDER)

    def test_rejects_unknown_or_modified_no_js_classic_placeholder(self) -> None:
        main = "web/default/dist/static/js/index.a935d36b34.js"
        for data in (
            b"<!doctype html><html><body>Some other placeholder</body></html>",
            CLASSIC_BUILD_PLACEHOLDER.replace(b"not available", b"unavailable"),
            CLASSIC_BUILD_PLACEHOLDER.replace(b"\n", b"\r\n"),
        ):
            with self.subTest(data=data):
                self.files = rspack_placeholder_fixture_files()
                self.files["web/classic/dist/index.html"] = data
                self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
                self.assert_refused(public_asset_sha256={main: hashlib.sha256(self.files[main]).hexdigest()})

    def test_exact_classic_profile_does_not_relax_default_main_proof(self) -> None:
        self.files = rspack_placeholder_fixture_files()
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        main = "web/default/dist/static/js/index.a935d36b34.js"
        self.assert_refused(public_asset_sha256={main: "0" * 64})
        self.assert_refused(public_asset_sha256={})
        self.files["web/default/dist/index.html"] = CLASSIC_BUILD_PLACEHOLDER
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        self.assert_refused(public_asset_sha256={main: hashlib.sha256(self.files[main]).hexdigest()})

    def test_rejects_hash_corruption_even_if_public_entry_is_valid(self) -> None:
        offset = self.records["web/classic/dist/assets/main.css"] + 32
        self.binary_bytes[offset] ^= 1
        self.assert_refused()

    def test_rejects_unmapped_data_pointer_not_bss_or_zero_padding(self) -> None:
        struct.pack_into("<Q", self.binary_bytes, self.records["web/default/dist/assets/main.css"] + 16, 0x900000)
        self.assert_refused()

    def test_rejects_incomplete_table_length(self) -> None:
        header = self.headers[0]
        length = struct.unpack_from("<Q", self.binary_bytes, header + 8)[0]
        struct.pack_into("<QQ", self.binary_bytes, header + 8, length - 1, length - 1)
        self.assert_refused()

    def test_rejects_truncated_non_entry_file_not_hidden_by_valid_index(self) -> None:
        self.files["web/default/dist/assets/zzzz.txt"] = b"not an HTML entry"
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        header = self.headers[0]
        length = struct.unpack_from("<Q", self.binary_bytes, header + 8)[0]
        struct.pack_into("<QQ", self.binary_bytes, header + 8, length - 1, length - 1)
        self.assert_refused()

    def test_rejects_orphan_larger_than_copy_bound_without_ignoring_it(self) -> None:
        self.files["web/default/dist/assets/zzzz.txt"] = b"hidden" * 800
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        header = self.headers[0]
        length = struct.unpack_from("<Q", self.binary_bytes, header + 8)[0]
        struct.pack_into("<QQ", self.binary_bytes, header + 8, length - 1, length - 1)
        self.assert_refused(limits=extractor.Limits(max_file_bytes=2000))

    def test_rejects_orphan_name_larger_than_copy_name_bound(self) -> None:
        parent = "web/default/dist/" + "/".join(["z" * 180] * 5)
        self.files[parent + "/a.txt"] = b"keep directory closure"
        self.files[parent + "/" + "z" * 230 + ".txt"] = b"hidden long name"
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        header = self.headers[0]
        length = struct.unpack_from("<Q", self.binary_bytes, header + 8)[0]
        struct.pack_into("<QQ", self.binary_bytes, header + 8, length - 1, length - 1)
        self.assert_refused()

    def test_rejects_length_capacity_disagreement(self) -> None:
        header = self.headers[0]
        struct.pack_into("<Q", self.binary_bytes, header + 16, 1)
        self.assert_refused()

    def test_rejects_incorrect_record_sort_order(self) -> None:
        first = self.records["web/default/dist/assets/main.js"]
        second = self.records["web/default/dist/assets/main.css"]
        left = bytes(self.binary_bytes[first : first + 48])
        self.binary_bytes[first : first + 48] = self.binary_bytes[second : second + 48]
        self.binary_bytes[second : second + 48] = left
        self.assert_refused()

    def test_rejects_missing_required_root_or_entry_asset(self) -> None:
        for name in ("web/classic/dist/index.html", "web/default/dist/assets/main.js"):
            with self.subTest(missing=name):
                self.binary_bytes, self.records, self.headers = elf_fixture(
                    {key: value for key, value in self.files.items() if key != name}
                )
                self.assert_refused()

    def test_rejects_path_traversal_and_backslash(self) -> None:
        for name in ("web/default/dist/../../escape", "web/default/dist/assets\\escape"):
            with self.subTest(name=name):
                self.binary_bytes, self.records, self.headers = elf_fixture({**self.files, name: b"bad"})
                self.assert_refused()

    def test_rejects_portable_duplicate_paths_and_directory_aliases(self) -> None:
        for name in ("web/default/dist/assets/MAIN.js", "web/default/dist/ASSETS/other.js"):
            with self.subTest(name=name):
                self.binary_bytes, self.records, self.headers = elf_fixture({**self.files, name: b"bad"})
                self.assert_refused()

    def test_rejects_binary_and_compiler_identity_mismatch(self) -> None:
        self.assert_refused(expected_binary_sha256="0" * 64)
        self.assert_refused(expected_go_version="go1.26.2")
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files, version="go1.27.1")
        self.assert_refused(expected_go_version="go1.27.1")

    def test_rejects_wrong_public_asset_hash_and_missing_independent_proof(self) -> None:
        self.assert_refused(public_asset_sha256={})
        self.assert_refused(public_asset_sha256={"web/default/dist/assets/main.js": "0" * 64})
        self.assert_refused(public_asset_sha256={"web/default/dist/assets/main.css": hashlib.sha256(self.files["web/default/dist/assets/main.css"]).hexdigest()})

    def test_rejects_existing_output_without_touching_it(self) -> None:
        self.output.mkdir()
        marker = self.output / "existing.txt"
        marker.write_text("retain")
        with self.assertRaises(extractor.ExtractionError):
            self.run_extract()
        self.assertEqual(marker.read_text(), "retain")

    def test_rejects_output_symlink(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unsupported")
        actual = self.root / "other"
        actual.mkdir()
        try:
            self.output.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(extractor.ExtractionError):
            self.run_extract()
        self.assertEqual(list(actual.iterdir()), [])

    def test_rejects_file_record_or_byte_limits_before_output(self) -> None:
        self.assert_refused(limits=extractor.Limits(max_file_bytes=10))
        self.assert_refused(limits=extractor.Limits(max_total_bytes=30))
        self.assert_refused(limits=extractor.Limits(max_records=2))
        self.assert_refused(limits=extractor.Limits(max_binary_bytes=100))

    def test_rejects_nonfinite_or_unbounded_resource_limits(self) -> None:
        for index, limits in enumerate((
            extractor.Limits(max_seconds=float("nan")),
            extractor.Limits(max_seconds=float("inf")),
            extractor.Limits(max_records=10**20),
            extractor.Limits(max_file_bytes=0),
        )):
            with self.subTest(limits=limits):
                self.output = self.root / f"owned-assets-{index}"
                self.assert_refused(limits=limits)

    def test_rejects_unsupported_elf_and_overlapping_address_mapping(self) -> None:
        self.binary_bytes[5] = 2
        self.assert_refused()
        self.binary_bytes, self.records, self.headers = elf_fixture(self.files)
        struct.pack_into("<Q", self.binary_bytes, 120 + 16, 0x500000)
        self.assert_refused()

    def test_rejects_detached_file_table_without_fs_owner(self) -> None:
        self.binary_bytes[0x10000 : 0x10010] = b"\x00" * 16
        self.assert_refused()

    def test_refuses_insufficient_disk_space_before_any_output(self) -> None:
        with mock.patch.object(extractor.shutil, "disk_usage", return_value=shutil_usage(0)):
            self.assert_refused()

    def test_cleans_only_new_output_after_write_io_failure(self) -> None:
        unrelated = self.root / "keep.txt"
        unrelated.write_text("retain")
        with mock.patch.object(extractor.os, "fdopen", side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                self.run_extract()
        self.assertFalse(self.output.exists())
        self.assertEqual(unrelated.read_text(), "retain")


def shutil_usage(free: int) -> object:
    """Return the public disk_usage shape without depending on its private type."""
    from types import SimpleNamespace
    return SimpleNamespace(total=free, used=0, free=free)


if __name__ == "__main__":
    unittest.main()
