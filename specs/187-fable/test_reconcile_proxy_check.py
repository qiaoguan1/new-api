"""No-live-change reconciliation retains its replay fence on partial failures."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


sp = importlib.util.spec_from_file_location("reconcile", Path(__file__).with_name("reconcile_proxy_check.py"))
r = importlib.util.module_from_spec(sp)
sp.loader.exec_module(r)


class ReconcileTests(unittest.TestCase):
    def prepare(self, path):
        root, release = path / "private", path / "release"
        root.mkdir(mode=0o700); release.mkdir()
        for name in ["proxy-mount-before.json", "proxy-main-nginx.conf", "proxy-mount-started.json"]:
            (root / name).write_text("test")
        (release / "proxy-maintain.log").write_text("test")
        return root, release

    def test_receipt_is_written_before_releasing_replay_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.prepare(Path(directory))
            def writer(path, data):
                self.assertTrue((root / "proxy-mount-started.json").exists())
                path.write_text(json.dumps(data))
            r.archive(root, release, writer)
            self.assertTrue((root / r.ARCHIVE / "proxy-mount-started.json").exists())
            self.assertTrue((root / r.RECEIPT).exists())

    def test_failed_partial_move_never_releases_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.prepare(Path(directory))
            calls = []
            def move(old, new):
                calls.append(old)
                if len(calls) == 2:
                    raise OSError("test")
                old.rename(new)
            with self.assertRaises(OSError):
                r.archive(root, release, lambda *_: None, move)
            self.assertTrue((root / "proxy-mount-started.json").exists())

    def test_existing_receipt_or_archive_prevents_any_move(self):
        for target in [r.RECEIPT, r.ARCHIVE]:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root, release = self.prepare(Path(directory))
                (root / target).write_text("existing")
                with self.assertRaises(RuntimeError):
                    r.archive(root, release, lambda *_: None)
                self.assertTrue((root / "proxy-mount-before.json").exists())

    def test_unsafe_root_blocks_before_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = Path(directory) / "file", Path(directory) / "release"
            root.write_text("not a directory"); release.mkdir()
            with self.assertRaisesRegex(RuntimeError, "roots unsafe"):
                r.archive(root, release, lambda *_: None)

    def test_failed_receipt_keeps_replay_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.prepare(Path(directory))
            def writer(*_):
                raise OSError("test")
            with self.assertRaises(OSError):
                r.archive(root, release, writer)
            self.assertTrue((root / "proxy-mount-started.json").exists())

    def test_second_failed_check_archives_only_its_exact_permanent_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.prepare(Path(directory))
            stack = Path(directory) / "stack"
            (stack / "nginx").mkdir(parents=True)
            permanent = stack / "nginx/issue187-main-nginx.conf"
            permanent.write_text("owned")
            with patch.object(r, "STACK", stack):
                r.archive(root, release, lambda path, data: path.write_text(json.dumps(data)), attempt=2)
            self.assertTrue((root / "proxy-cancelled-host-check-2" / permanent.name).exists())
            self.assertTrue((root / "proxy-main-nginx.conf").exists())
            self.assertTrue((root / "proxy-check-reconciled-2.json").exists())


if __name__ == "__main__":
    unittest.main()
