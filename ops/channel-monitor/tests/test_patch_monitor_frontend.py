import sys
import tempfile
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from patch_monitor_frontend import (  # noqa: E402
    APP_HEALTH_NEW,
    APP_HEALTH_OLD,
    APP_SUMMARY_NEW,
    APP_SUMMARY_OLD,
    CSS_NEW,
    CSS_OLD,
    HTML_FILTER_NEW,
    HTML_FILTER_OLD,
    patch_files,
)


class PatchMonitorFrontendTests(unittest.TestCase):
    def make_root(self, path: Path):
        (path / "app.js").write_text(APP_HEALTH_OLD + APP_SUMMARY_OLD, encoding="utf-8")
        (path / "index.html").write_text(HTML_FILTER_OLD, encoding="utf-8")
        (path / "styles.css").write_text(CSS_OLD, encoding="utf-8")

    def test_adds_new_states_coverage_and_warning_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_root(root)
            self.assertTrue(patch_files(root))
            self.assertIn(APP_HEALTH_NEW, (root / "app.js").read_text(encoding="utf-8"))
            self.assertIn(APP_SUMMARY_NEW, (root / "app.js").read_text(encoding="utf-8"))
            self.assertIn(HTML_FILTER_NEW, (root / "index.html").read_text(encoding="utf-8"))
            self.assertIn(CSS_NEW, (root / "styles.css").read_text(encoding="utf-8"))
            self.assertFalse(patch_files(root))

    def test_refuses_unknown_app_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_root(root)
            (root / "app.js").write_text("unknown", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "health labels"):
                patch_files(root)


if __name__ == "__main__":
    unittest.main()
