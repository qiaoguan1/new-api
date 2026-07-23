import datetime
import importlib.util
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "ops/channel-monitor/scripts"
TIME_MODULE = SCRIPTS / "monitor_time.py"
PATCHER = SCRIPTS / "patch_beijing_time_policy.py"
FETCH_WORKER = SCRIPTS / "fetch-upstream-balance.py"
PRICING_WORKER = SCRIPTS / "auto-apply-pricing.py"
MODEL_STATUS_PAGE = ROOT / "web/default/src/features/channel-monitor/index.tsx"
README = ROOT / "ops/channel-monitor/README.md"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BeijingBusinessTimeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TIME_MODULE.exists(), "shared Beijing-time module is required")
        self.time_policy = load_module(TIME_MODULE, "monitor_time_under_test")

    def test_previous_complete_day_uses_beijing_date_after_utc_16_boundary(self):
        instant = datetime.datetime(
            2026, 7, 23, 16, 30, tzinfo=datetime.timezone.utc
        )

        self.assertEqual(
            self.time_policy.previous_complete_beijing_day(instant), "2026-07-23"
        )

    def test_epoch_partition_changes_at_beijing_midnight(self):
        before_midnight = int(
            datetime.datetime(
                2026, 7, 22, 15, 59, 59, tzinfo=datetime.timezone.utc
            ).timestamp()
        )
        midnight = int(
            datetime.datetime(
                2026, 7, 22, 16, 0, 0, tzinfo=datetime.timezone.utc
            ).timestamp()
        )

        self.assertEqual(
            self.time_policy.beijing_day_for_epoch(before_midnight), "2026-07-22"
        )
        self.assertEqual(
            self.time_policy.beijing_day_for_epoch(midnight), "2026-07-23"
        )

    def test_iso_timestamp_has_explicit_eight_hour_offset(self):
        instant = datetime.datetime(
            2026, 7, 23, 16, 30, 45, tzinfo=datetime.timezone.utc
        )

        self.assertEqual(
            self.time_policy.beijing_iso(instant), "2026-07-24T00:30:45+08:00"
        )

    def test_invalid_or_naive_dates_fail_closed(self):
        with self.assertRaises(ValueError):
            self.time_policy.resolve_beijing_business_day("2026-02-30")
        with self.assertRaises(ValueError):
            self.time_policy.previous_complete_beijing_day(
                datetime.datetime(2026, 7, 24, 0, 30)
            )


class BeijingTimeSourcePolicyTests(unittest.TestCase):
    def test_tracked_workers_share_one_beijing_day_policy(self):
        fetch = FETCH_WORKER.read_text(encoding="utf-8")
        pricing = PRICING_WORKER.read_text(encoding="utf-8")

        for source in (fetch, pricing):
            self.assertIn("from monitor_time import", source)
            self.assertIn("resolve_beijing_business_day", source)
            self.assertNotIn("target_utc_day", source)
            self.assertNotIn("timezone.utc", source)
        self.assertIn("beijing_day_for_epoch", fetch)
        self.assertIn("beijing_iso_now", fetch)

    def test_customer_page_formats_and_labels_beijing_time(self):
        source = MODEL_STATUS_PAGE.read_text(encoding="utf-8")

        self.assertIn("timeZone: 'Asia/Shanghai'", source)
        self.assertIn("北京时间", source)
        self.assertNotIn("updatedAt.toLocaleTimeString", source)

    def test_documented_cron_contract_is_explicit(self):
        source = README.read_text(encoding="utf-8")

        self.assertIn("CRON_TZ=Asia/Shanghai", source)
        self.assertIn("0 * * * * generate-monitor-data.py", source)
        self.assertNotIn("previous complete UTC", source)

    def test_production_patcher_covers_audit_generator_and_internal_ui(self):
        self.assertTrue(PATCHER.exists(), "production timezone patcher is required")
        source = PATCHER.read_text(encoding="utf-8")

        self.assertIn("scan-upstream-daily.py", source)
        self.assertIn("generate-monitor-data.py", source)
        self.assertIn("app.js", source)
        self.assertIn("AT TIME ZONE 'Asia/Shanghai'", source)
        self.assertIn('timeZone: "Asia/Shanghai"', source)


class BeijingTimeProductionPatcherTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATCHER.exists(), "production timezone patcher is required")
        self.patcher = load_module(PATCHER, "beijing_time_patcher_under_test")

    def test_scan_worker_patch_is_idempotent_and_uses_beijing_sql(self):
        source = """from datetime import datetime, timedelta, timezone

def now_local():
    return datetime.now(timezone.utc).astimezone()


def now_iso():
    return now_local().isoformat(timespec=\"seconds\")


def target_utc_day():
    \"\"\"默认审计上一个完整 UTC 日；可用 CHANNEL_MONITOR_DAY=YYYY-MM-DD 覆盖。\"\"\"
    override = os.environ.get(\"CHANNEL_MONITOR_DAY\", \"\").strip()
    if override:
        datetime.strptime(override, \"%Y-%m-%d\")
        return override
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

day = target_utc_day()
sql = \"(to_timestamp(created_at) AT TIME ZONE 'UTC')::date\"
"""

        patched = self.patcher.patch_scan_source(source)

        self.assertEqual(self.patcher.patch_scan_source(patched), patched)
        self.assertIn("resolve_beijing_business_day", patched)
        self.assertIn("beijing_iso_now", patched)
        self.assertIn("target_beijing_day", patched)
        self.assertIn("AT TIME ZONE 'Asia/Shanghai'", patched)
        self.assertNotIn("target_utc_day", patched)

    def test_generator_and_internal_ui_patch_are_idempotent(self):
        generator = '''from datetime import datetime, timezone

def local_now():
    return datetime.now(timezone.utc).astimezone()

    \"\"\"按完整 UTC 日核算站内收入、上游人民币真实成本和渠道毛利。\"\"\"
    WHERE (to_timestamp(created_at) AT TIME ZONE 'UTC')::date = '{day}'::date
        \"generated_at_iso\": datetime.now(timezone.utc).astimezone().isoformat(timespec=\"seconds\"),
'''
        app = '''dateBox.textContent = `核对 UTC 日期：${reconciliation.date || business?.date || \"-\"}`;
return new Date(n * 1000).toLocaleString(\"zh-CN\", { hour12: false });
'''

        patched_generator = self.patcher.patch_generate_source(generator)
        patched_app = self.patcher.patch_app_source(app)

        self.assertEqual(
            self.patcher.patch_generate_source(patched_generator), patched_generator
        )
        self.assertEqual(self.patcher.patch_app_source(patched_app), patched_app)
        self.assertIn("AT TIME ZONE 'Asia/Shanghai'", patched_generator)
        self.assertIn("beijing_iso_now()", patched_generator)
        self.assertIn("核对北京时间业务日", patched_app)
        self.assertIn('timeZone: "Asia/Shanghai"', patched_app)

    def test_atomic_writer_preserves_existing_file_mode(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = pathlib.Path(temporary_directory) / "worker.py"
            target.write_text("old\n", encoding="utf-8")
            target.chmod(0o750)
            original_mode = stat.S_IMODE(target.stat().st_mode)

            self.patcher.write_atomic(target, "new\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)
            self.assertIn(
                "temporary.chmod(original_mode)", PATCHER.read_text(encoding="utf-8")
            )
            if os.name != "nt":
                self.assertEqual(original_mode, 0o750)

    def test_apply_validates_every_target_before_writing_any_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            scripts = root / "scripts"
            scripts.mkdir()
            scan = scripts / "scan-upstream-daily.py"
            generator = scripts / "generate-monitor-data.py"
            app = root / "app.js"
            for path in (scan, generator, app):
                path.write_text(f"original:{path.name}\n", encoding="utf-8")
            originals = {
                path: path.read_text(encoding="utf-8") for path in (scan, generator, app)
            }

            with (
                mock.patch.object(
                    self.patcher,
                    "patch_scan_source",
                    side_effect=lambda source: source + "patched-scan\n",
                ),
                mock.patch.object(
                    self.patcher,
                    "patch_generate_source",
                    side_effect=lambda source: source + "patched-generator\n",
                ),
                mock.patch.object(
                    self.patcher,
                    "patch_app_source",
                    side_effect=self.patcher.PatchError("invalid app anchor"),
                ),
            ):
                with self.assertRaises(self.patcher.PatchError):
                    self.patcher.apply(root)

            for path, original in originals.items():
                self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
