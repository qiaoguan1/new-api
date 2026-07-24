import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from patch_monitor_generator import (  # noqa: E402
    AGGREGATION_NEW,
    AGGREGATION_OLD,
    DB_OLD,
    HEALTH_OLD,
    IMPORT_NEW,
    IMPORT_OLD,
    PAYLOAD_OLD,
    TOTALS_BLOCK_NEW,
    TOTALS_BLOCK_OLD,
    TOTALS_OLD,
    UNMATCHED_NEW,
    UNMATCHED_OLD,
    patch_text,
)


class PatchMonitorGeneratorTests(unittest.TestCase):
    def fixture(self):
        return "\n".join(
            [
                IMPORT_OLD,
                HEALTH_OLD,
                DB_OLD,
                AGGREGATION_OLD,
                UNMATCHED_OLD,
                TOTALS_BLOCK_OLD.replace(
                    '        "alerts": sum(1 for row in rows if is_alert(row["health"])),\n'
                    '        "warnings": sum(1 for row in rows if is_warning(row["health"])),\n',
                    TOTALS_OLD,
                ),
                PAYLOAD_OLD,
            ]
        )

    def test_patches_all_required_contracts(self):
        updated, changed = patch_text(self.fixture())
        self.assertTrue(changed)
        self.assertIn(IMPORT_NEW, updated)
        self.assertIn(AGGREGATION_NEW, updated)
        self.assertIn(UNMATCHED_NEW, updated)
        self.assertIn(TOTALS_BLOCK_NEW, updated)
        self.assertNotIn(HEALTH_OLD, updated)

    def test_is_idempotent(self):
        updated, _ = patch_text(self.fixture())
        repeated, changed = patch_text(updated)
        self.assertFalse(changed)
        self.assertEqual(repeated, updated)

    def test_refuses_unknown_source_shape(self):
        with self.assertRaisesRegex(ValueError, "policy import"):
            patch_text("def build(): pass\n")


if __name__ == "__main__":
    unittest.main()
