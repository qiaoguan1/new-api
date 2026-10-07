"""Offline cancellation archival contracts: no Docker, SSH or production."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("resume_runtime", Path(__file__).with_name("resume_runtime.py"))
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


def writer(path, value):
    """Exclusive fixture writer matching the production evidence contract."""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())


class ResumeTests(unittest.TestCase):
    def fixture(self, directory):
        root, release = Path(directory) / "private", Path(directory) / "release"
        root.mkdir(mode=0o700)
        release.mkdir(mode=0o700)
        for name in resume.PHASES:
            writer(root / name, {"task": "owned"})
        (release / "runtime-deploy.log").write_text("owned log")
        return root, release

    def test_cancellation_requires_strict_no_swap_restoration_and_bound_baseline(self):
        baseline = "sha256:" + "a" * 64
        before, started = {"native_image": baseline}, {"baseline_image": baseline, "at": 10}
        cancellation = {"binary_switch_attempted": False, "ingress_released": True, "at": 11}
        resume.validate_cancellation(cancellation, before, started, baseline)
        for changes in [{"binary_switch_attempted": True}, {"binary_switch_attempted": 0},
                        {"ingress_released": 1}, {"at": 9}]:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                resume.validate_cancellation({**cancellation, **changes}, before, started, baseline)
        with self.assertRaises(RuntimeError):
            resume.validate_cancellation(cancellation, {"native_image": "different"}, started, baseline)

    def test_all_sources_prechecked_before_any_directory_or_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.fixture(directory)
            (release / "runtime-deploy.log").unlink()
            with self.assertRaises(RuntimeError):
                resume.archive_cancelled(root, release, writer)
            self.assertFalse((root / resume.ARCHIVE).exists())
            self.assertTrue((root / "runtime-started.json").exists())

    def test_existing_receipt_archive_and_ambiguous_recovery_block_without_moves(self):
        for name in [resume.RECEIPT, resume.ARCHIVE, "runtime-recovery-required.json", "runtime-ready.json", "runtime-deployed.json"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root, release = self.fixture(directory)
                writer(root / name, {"existing": True})
                with self.assertRaises(RuntimeError):
                    resume.archive_plan(root, release)
                self.assertTrue((root / "runtime-started.json").exists())

    def test_partial_rename_failure_retains_replay_fence_and_cannot_repeat_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.fixture(directory)
            calls = []
            def fail_second(source, destination):
                calls.append(source.name)
                if len(calls) == 2:
                    raise OSError("fixture failure")
                source.rename(destination)
            with self.assertRaises(OSError):
                resume.archive_cancelled(root, release, writer, fail_second)
            self.assertTrue((root / "runtime-started.json").exists())
            self.assertFalse((root / resume.RECEIPT).exists())
            with self.assertRaises(RuntimeError):
                resume.archive_plan(root, release)

    def test_receipt_failure_retains_fence_after_other_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.fixture(directory)
            def fail_writer(path, value):
                raise OSError("fixture receipt failure")
            with self.assertRaises(OSError):
                resume.archive_cancelled(root, release, fail_writer)
            self.assertTrue((root / "runtime-started.json").exists())
            self.assertTrue((root / resume.ARCHIVE / "runtime-before.json").is_file())

    def test_success_writes_receipt_before_releasing_original_fence_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.fixture(directory)
            moves = []
            def move(source, destination):
                if source.name == "runtime-started.json":
                    self.assertTrue((root / resume.RECEIPT).is_file())
                moves.append(source.name)
                source.rename(destination)
            resume.archive_cancelled(root, release, writer, move)
            self.assertEqual(moves[-1], "runtime-started.json")
            self.assertFalse((root / "runtime-started.json").exists())
            self.assertEqual(len(list((root / resume.ARCHIVE).iterdir())), 4)
            with self.assertRaises(RuntimeError):
                resume.archive_plan(root, release)

    def test_symlink_source_or_dangling_destination_never_follows_or_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root, release = self.fixture(directory)
            original = root / "runtime-before.json"
            original.unlink()
            try:
                original.symlink_to(root / "runtime-started.json")
            except OSError as error:
                self.skipTest("fixture platform has no symlink privilege: " + type(error).__name__)
            with self.assertRaises(RuntimeError):
                resume.archive_plan(root, release)
            original.unlink()
            writer(original, {})
            (root / resume.RECEIPT).symlink_to(root / "missing")
            with self.assertRaises(RuntimeError):
                resume.archive_plan(root, release)


if __name__ == "__main__":
    unittest.main()
