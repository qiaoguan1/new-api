import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "install_newapi_backup.py"
SPEC = importlib.util.spec_from_file_location("install_newapi_backup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class InstallBackupTests(unittest.TestCase):
    def test_render_crontab_preserves_jobs_and_adds_beijing_backup(self) -> None:
        existing = "CRON_TZ=Asia/Shanghai\n0 * * * * hourly-job\n"
        rendered = MODULE.render_crontab(existing)

        self.assertIn("0 * * * * hourly-job", rendered)
        self.assertIn("30 3 * * *", rendered)
        self.assertIn("/run/lock/newapi-daily-backup.lock", rendered)
        self.assertIn("/var/log/newapi-daily-backup.log", rendered)
        self.assertEqual(rendered.count(MODULE.BEGIN_MARKER), 1)

    def test_render_crontab_is_idempotent(self) -> None:
        existing = "14 23 * * * certificate-job\n"
        first = MODULE.render_crontab(existing)
        second = MODULE.render_crontab(first)
        self.assertEqual(first, second)

    def test_render_crontab_replaces_one_existing_managed_block(self) -> None:
        existing = (
            "0 * * * * keep-me\n"
            f"{MODULE.BEGIN_MARKER}\n"
            "0 0 * * * obsolete-backup\n"
            f"{MODULE.END_MARKER}\n"
        )
        rendered = MODULE.render_crontab(existing)
        self.assertNotIn("obsolete-backup", rendered)
        self.assertIn("0 * * * * keep-me", rendered)

    def test_render_crontab_rejects_nul_and_unbalanced_markers(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.render_crontab("job\x00bad\n")
        with self.assertRaises(ValueError):
            MODULE.render_crontab(f"{MODULE.BEGIN_MARKER}\nno end\n")

    def test_parse_crontab_result_distinguishes_absence_from_read_error(self) -> None:
        self.assertEqual(
            MODULE.parse_crontab_result(1, "", "no crontab for root\n"), ""
        )
        self.assertEqual(MODULE.parse_crontab_result(0, "job\n", ""), "job\n")
        with self.assertRaises(RuntimeError):
            MODULE.parse_crontab_result(1, "", "permission denied\n")


if __name__ == "__main__":
    unittest.main()
