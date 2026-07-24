import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from patch_auto_pricing_recent_costs import (  # noqa: E402
    COLLECT_NEW,
    COLLECT_OLD,
    CONSTANT_NEW,
    CONSTANT_OLD,
    DISCOVERY_NEW,
    DISCOVERY_OLD,
    FIXED_FIELDS_NEW,
    FIXED_FIELDS_OLD,
    FIXED_UNPACK_NEW,
    FIXED_UNPACK_OLD,
    IMPORT_NEW,
    IMPORT_OLD,
    SUMMARY_NEW,
    SUMMARY_OLD,
    TEXT_FIELDS_NEW,
    TEXT_FIELDS_OLD,
    TEXT_UNPACK_NEW,
    TEXT_UNPACK_OLD,
    patch_text,
)


class PatchRecentCostsTests(unittest.TestCase):
    def fixture(self):
        return "\n".join(
            [
                IMPORT_OLD,
                CONSTANT_OLD,
                COLLECT_OLD,
                TEXT_UNPACK_OLD,
                TEXT_FIELDS_OLD,
                FIXED_UNPACK_OLD,
                FIXED_FIELDS_OLD,
                SUMMARY_OLD,
                DISCOVERY_OLD,
            ]
        )

    def test_patches_selection_evidence_and_summary(self):
        updated, changed = patch_text(self.fixture())
        self.assertTrue(changed)
        for expected in (
            IMPORT_NEW,
            CONSTANT_NEW,
            COLLECT_NEW,
            TEXT_UNPACK_NEW,
            TEXT_FIELDS_NEW,
            FIXED_UNPACK_NEW,
            FIXED_FIELDS_NEW,
            SUMMARY_NEW,
            DISCOVERY_NEW,
        ):
            self.assertIn(expected, updated)

    def test_can_finish_a_partially_patched_production_worker(self):
        fully_patched, _ = patch_text(self.fixture())
        stage_one = fully_patched.replace(DISCOVERY_NEW, DISCOVERY_OLD)
        updated, changed = patch_text(stage_one)
        self.assertTrue(changed)
        self.assertIn(DISCOVERY_NEW, updated)

    def test_is_idempotent(self):
        updated, _ = patch_text(self.fixture())
        repeated, changed = patch_text(updated)
        self.assertFalse(changed)
        self.assertEqual(repeated, updated)

    def test_refuses_unknown_worker_shape(self):
        with self.assertRaisesRegex(ValueError, "recent-cost import"):
            patch_text("print('unknown')\n")


if __name__ == "__main__":
    unittest.main()
