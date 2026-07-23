import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).parents[1] / "scripts" / "patch_scan_upstream_metadata_probe.py"
)
SPEC = importlib.util.spec_from_file_location("patch_scan_metadata_probe", MODULE_PATH)
PATCHER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PATCHER)


class MetadataProbePatcherTests(unittest.TestCase):
    def test_replacement_is_idempotent_when_new_block_contains_old_anchor(self):
        old = "return result\n\n\ndef fetch_pricing(base_url):"
        new = "return result\n\n\ndef metadata_probe():\n    pass\n\n\ndef fetch_pricing(base_url):"
        source = f"prefix\n{old}\nsuffix\n"

        patched = PATCHER._replace_once_or_verify(source, old, new, "test")

        self.assertEqual(
            PATCHER._replace_once_or_verify(patched, old, new, "test"), patched
        )

    def test_ambiguous_legacy_anchors_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "expected one legacy"):
            PATCHER._replace_once_or_verify("old old", "old", "new", "test")

    def test_metadata_probe_normalizes_base_url_with_existing_v1_path(self):
        self.assertIn(
            'models_path = "/models" if base_path.endswith("/v1") else "/v1/models"',
            PATCHER.METADATA_PROBE_NEW,
        )

    def test_audit_loop_never_falls_back_to_paid_probe(self):
        self.assertIn(
            "probe = metadata_probe_channel(channel, ledger_entry)", PATCHER.LOOP_NEW
        )
        self.assertNotIn("probe_channel(channel)", PATCHER.LOOP_NEW)


if __name__ == "__main__":
    unittest.main()
