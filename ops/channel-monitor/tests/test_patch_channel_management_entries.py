import pathlib
import sys
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from patch_channel_management_entries import SUMMARY_V1, TOOLBAR_NEW, transform  # noqa: E402


class ChannelManagementEntryPatchTests(unittest.TestCase):
    def test_replaces_ambiguous_toolbar_with_three_explicit_entries(self):
        source = """<div class="toolbar">
          <button id="manageBtn" type="button" class="secondary-btn">管理渠道</button>
          <a href="./upstreams-admin.html" class="secondary-btn" style="text-decoration:none;display:inline-flex;align-items:center;">上游凭证</a>
        </div>
      </header>

      <section class="summary" id="summary"></section>"""

        updated = transform(source)

        self.assertIn('href="/channels?action=create"', updated)
        self.assertIn('href="/channels"', updated)
        self.assertIn('href="./upstreams-admin.html"', updated)
        self.assertIn('id="manageBtn"', updated)
        self.assertIn("新增渠道需要两步", updated)
        self.assertIn("上游名 · 用途", updated)
        self.assertNotIn(">管理渠道</button>", updated)

    def test_is_idempotent_after_the_entries_are_patched(self):
        source = """<div class="toolbar">
          <button id="manageBtn" type="button" class="secondary-btn">管理渠道</button>
          <a href="./upstreams-admin.html" class="secondary-btn" style="text-decoration:none;display:inline-flex;align-items:center;">上游凭证</a>
        </div>
      </header>

      <section class="summary" id="summary"></section>"""

        updated = transform(source)

        self.assertEqual(transform(updated), updated)

    def test_upgrades_the_already_deployed_two_step_guide(self):
        source = f"""<div class="toolbar">
{TOOLBAR_NEW}
        </div>
{SUMMARY_V1}"""

        updated = transform(source)

        self.assertIn("上游名 · 用途", updated)
        self.assertNotIn(SUMMARY_V1, updated)

    def test_rejects_partial_or_unknown_markup(self):
        with self.assertRaises(RuntimeError):
            transform('href="/channels?action=create"')


if __name__ == "__main__":
    unittest.main()
