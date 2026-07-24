import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "sanitize_crontab.py"
SPEC = importlib.util.spec_from_file_location("sanitize_crontab", MODULE_PATH)
sanitizer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sanitizer)


class CrontabSanitizerTests(unittest.TestCase):
    def test_normalizes_windows_and_bare_carriage_returns(self):
        source = (
            b"CRON_TZ=Asia/Shanghai\r\n"
            b"20 8 * * * fetch.py\r\n"
            b"40 8 * * * auto-apply-pricing.py >> /var/log/auto-pricing.log 2>&1\r"
        )

        normalized = sanitizer.normalize_crontab_bytes(source)

        self.assertEqual(
            normalized,
            b"CRON_TZ=Asia/Shanghai\n"
            b"20 8 * * * fetch.py\n"
            b"40 8 * * * auto-apply-pricing.py >> /var/log/auto-pricing.log 2>&1\n",
        )
        self.assertNotIn(b"\r", normalized)

    def test_preserves_content_and_adds_one_terminal_newline(self):
        source = b"0 * * * * generate-monitor-data.py\n\n"

        normalized = sanitizer.normalize_crontab_bytes(source)

        self.assertEqual(normalized, b"0 * * * * generate-monitor-data.py\n")

    def test_removes_literal_backslash_r_suffix_from_cron_line(self):
        literal_backslash_r = bytes((92, 114))
        source = (
            b"40 8 * * * auto-apply-pricing.py "
            b">> /var/log/auto-pricing.log 2>&1"
            + literal_backslash_r
            + b"\n"
        )

        normalized = sanitizer.normalize_crontab_bytes(source)

        self.assertEqual(
            normalized,
            b"40 8 * * * auto-apply-pricing.py "
            b">> /var/log/auto-pricing.log 2>&1\n",
        )

    def test_rejects_empty_crontab_to_avoid_erasing_existing_jobs(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            sanitizer.normalize_crontab_bytes(b"\r\n\n")


if __name__ == "__main__":
    unittest.main()
