import pathlib
import sys
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from patch_reconciliation_ui_required import transform  # noqa: E402


class RequiredReconciliationUiPatchTests(unittest.TestCase):
    def test_replaces_legacy_summary_once(self):
        source = """summaryBox.innerHTML = `
    <span>完整 ${fmtInt(totals.complete_upstreams)} / ${fmtInt(totals.expected_upstreams)} 家</span>
    <span>未完成 ${fmtInt(totals.incomplete_upstreams)} 家</span>
    <span>缺凭证 ${fmtInt(totals.credentialless_upstreams)} 家</span>
`;"""

        updated = transform(source)

        self.assertIn("complete_required_upstreams", updated)
        self.assertIn("incomplete_required_upstreams", updated)
        self.assertIn("optional_upstreams", updated)
        self.assertNotIn("totals.complete_upstreams", updated)

    def test_rejects_already_patched_summary(self):
        with self.assertRaises(RuntimeError):
            transform("${fmtInt(totals.complete_required_upstreams)}")


if __name__ == "__main__":
    unittest.main()
