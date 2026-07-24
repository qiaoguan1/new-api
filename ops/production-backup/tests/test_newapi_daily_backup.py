import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "newapi_daily_backup.py"
SPEC = importlib.util.spec_from_file_location("newapi_daily_backup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if hasattr(kwargs.get("stdout"), "write"):
            kwargs["stdout"].write(b"PGDMP fake custom archive")
        if kwargs.get("stdin") is not None:
            self.assert_dump(kwargs["stdin"].read())
        return None

    @staticmethod
    def assert_dump(content: bytes) -> None:
        if not content.startswith(b"PGDMP"):
            raise AssertionError("dump was not custom format")


class DailyBackupTests(unittest.TestCase):
    def test_completed_child_validation_rejects_escape_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            valid = root / "newapi-20260724-033000"
            valid.mkdir()
            self.assertEqual(MODULE.validate_completed_child(root, valid), valid)
            with self.assertRaises(ValueError):
                MODULE.validate_completed_child(root, root.parent / valid.name)
            link = root / "newapi-20260723-033000"
            try:
                link.symlink_to(valid, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaises(ValueError):
                MODULE.validate_completed_child(root, link)

    def test_retention_removes_only_old_completed_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            names = [
                "newapi-20260720-033000",
                "newapi-20260721-033000",
                "newapi-20260722-033000",
                "newapi-20260723-033000",
            ]
            for name in names:
                (root / name).mkdir()
            (root / ".newapi-20260724-033000.tmp-1").mkdir()
            (root / "manual-keep").mkdir()

            removed = MODULE.prune_completed_backups(root, retain=2)

            self.assertEqual([path.name for path in removed], names[:2])
            self.assertTrue((root / names[2]).is_dir())
            self.assertTrue((root / names[3]).is_dir())
            self.assertTrue((root / "manual-keep").is_dir())
            self.assertTrue((root / ".newapi-20260724-033000.tmp-1").is_dir())

    def test_manifest_detects_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "database.pgdump"
            target.write_bytes(b"PGDMP original")
            MODULE.write_manifest(root, [target])
            MODULE.verify_manifest(root)
            target.write_bytes(b"PGDMP modified")
            with self.assertRaises(ValueError):
                MODULE.verify_manifest(root)

    def test_run_backup_publishes_verified_root_only_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = workspace / "stack"
            backup_root = workspace / "backups"
            (stack / "nginx" / "conf.d").mkdir(parents=True)
            (stack / "channel-monitor").mkdir()
            (stack / "secrets" / "wechatpay").mkdir(parents=True)
            (stack / ".env").write_text("SECRET=value\n", encoding="utf-8")
            (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (stack / "nginx" / "conf.d" / "default.conf").write_text("server {}\n", encoding="utf-8")
            (stack / "channel-monitor" / "upstreams.json").write_text("[]\n", encoding="utf-8")
            (stack / "secrets" / "wechatpay" / "key.pem").write_text("private\n", encoding="utf-8")
            runner = FakeRunner()

            result = MODULE.run_backup(
                stack_root=stack,
                backup_root=backup_root,
                retain=14,
                runner=runner,
                now=MODULE.datetime(2026, 7, 24, 3, 30, 0),
            )

            self.assertEqual(result.name, "newapi-20260724-033000")
            self.assertTrue((result / "database.pgdump").is_file())
            self.assertTrue((result / "recovery-config.tar.gz").is_file())
            self.assertTrue((result / "SHA256SUMS").is_file())
            MODULE.verify_manifest(result)
            if os.name != "nt":
                self.assertEqual(os.stat(result).st_mode & 0o777, 0o700)
                for child in result.iterdir():
                    self.assertEqual(os.stat(child).st_mode & 0o777, 0o600)
            self.assertEqual(len(runner.calls), 2)
            dump_hash = hashlib.sha256((result / "database.pgdump").read_bytes()).hexdigest()
            self.assertIn(dump_hash, (result / "SHA256SUMS").read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
