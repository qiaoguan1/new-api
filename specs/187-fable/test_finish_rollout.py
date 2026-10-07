"""Offline checks for durable non-replaying phase orchestration."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


sp = importlib.util.spec_from_file_location("finish", Path(__file__).with_name("finish_rollout.py"))
finish = importlib.util.module_from_spec(sp)
sp.loader.exec_module(finish)


class FinishTests(unittest.TestCase):
    def test_phase_is_reserved_and_executed_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(finish, "B", Path(directory)), \
                patch.object(finish.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result = {"success": False}
            finish.execute(["rollout.py", "stage", "--db", "new-api"], "stage", result)
            self.assertEqual(result["phase"], "stage")
            with self.assertRaises(FileExistsError):
                finish.execute(["rollout.py", "stage"], "stage", result)
            self.assertEqual(run.call_count, 1)

    def test_failure_does_not_retry_or_continue(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(finish, "B", Path(directory)), \
                patch.object(finish.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            result = {"success": False}
            with self.assertRaises(RuntimeError):
                finish.execute(["promote_keys.py"], "keys", result)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(result, {"success": False, "phase": "keys"})

    def test_progress_replaces_only_owned_progress(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(finish, "B", Path(directory)):
            finish.progress({"phase": "first"})
            finish.progress({"phase": "second"})
            self.assertEqual(json.loads((Path(directory)/"finish-rollout-progress.json").read_text()), {"phase": "second"})
            self.assertFalse((Path(directory)/"finish-rollout-progress.tmp").exists())

    def test_sync_uses_bounded_independent_intervals(self):
        state = {"now": 0}
        def sleep(seconds):
            self.assertLessEqual(seconds, 10)
            state["now"] += seconds
        with tempfile.TemporaryDirectory() as directory, patch.object(finish, "B", Path(directory)), \
                patch.object(finish.time, "monotonic", side_effect=lambda: state["now"]), \
                patch.object(finish.time, "sleep", side_effect=sleep):
            result = {}
            finish.synchronize(70, "sync", result)
            self.assertEqual(state["now"], 70)
            self.assertNotIn("sync_remaining_seconds", result)


if __name__ == "__main__":
    unittest.main()
