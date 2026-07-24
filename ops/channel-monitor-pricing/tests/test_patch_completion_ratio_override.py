import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from patch_completion_ratio_override import (  # noqa: E402
    NEW_GET,
    NEW_INFO,
    OLD_GET,
    OLD_INFO,
    patch_text,
)


class PatchCompletionRatioOverrideTests(unittest.TestCase):
    def test_explicit_ratio_precedes_locked_family_default(self):
        updated, changed = patch_text(OLD_GET + "\n" + OLD_INFO)
        self.assertTrue(changed)
        self.assertIn(NEW_GET, updated)
        self.assertIn(NEW_INFO, updated)
        self.assertNotIn('if strings.Contains(name, "/")', updated)

    def test_is_idempotent(self):
        updated, _ = patch_text(OLD_GET + "\n" + OLD_INFO)
        repeated, changed = patch_text(updated)
        self.assertFalse(changed)
        self.assertEqual(repeated, updated)

    def test_refuses_unknown_source(self):
        with self.assertRaisesRegex(ValueError, "completion ratio getter"):
            patch_text("package ratio_setting\n")


if __name__ == "__main__":
    unittest.main()
