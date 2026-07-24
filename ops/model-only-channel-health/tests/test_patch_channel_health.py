import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patch_channel_health import patch_text, validate_safe  # noqa: E402


LEGACY = "\n".join(
    (
        "api.get<MonitorData>('/api/channel-monitor')",
        "渠道收入、真实成本与毛利",
        "各上游模型真实成本",
        "上游运行排行",
        "每 5 分钟刷新",
        "模型性能（近 {hours} 小时）",
    )
)
SAFE = (ROOT / "sanitized_channel_monitor.tsx").read_text(encoding="utf-8")


class ChannelHealthPatchTests(unittest.TestCase):
    def test_replaces_known_legacy_page(self):
        patched, changed = patch_text(LEGACY, SAFE)
        self.assertTrue(changed)
        self.assertEqual(patched, SAFE)
        self.assertNotIn("/api/channel-monitor", patched)

    def test_is_idempotent(self):
        patched, changed = patch_text(SAFE, SAFE)
        self.assertFalse(changed)
        self.assertEqual(patched, SAFE)

    def test_refuses_unknown_page(self):
        with self.assertRaisesRegex(ValueError, "unknown legacy page shape"):
            patch_text("export function OtherPage() {}", SAFE)

    def test_refuses_template_with_private_fields(self):
        with self.assertRaisesRegex(ValueError, "unsafe template"):
            validate_safe(SAFE + "\nconst gross_margin = 1\n")


if __name__ == "__main__":
    unittest.main()
