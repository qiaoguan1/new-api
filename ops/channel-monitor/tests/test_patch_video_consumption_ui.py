import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "patch_video_consumption_ui.py"
SPEC = importlib.util.spec_from_file_location("patch_video_consumption_ui", SCRIPT)
patcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(patcher)


class VideoConsumptionUiPatchTests(unittest.TestCase):
    def test_html_patch_is_idempotent(self):
        source = '<main>\n      <section class="panel pricing-panel">\n</main>\n'
        once = patcher.patch_html(source)
        self.assertEqual(patcher.patch_html(once), once)
        self.assertEqual(once.count(patcher.HTML_MARKER), 1)
        self.assertIn('id="videoConsumptionRows"', once)

    def test_js_patch_is_idempotent_and_registers_renderer(self):
        source = '''function renderReconciliation() {}
function renderDynamicPricing(pricing) {}
function render() {
  renderReconciliation(state.data.daily_business);
}
'''
        once = patcher.patch_js(source)
        self.assertEqual(patcher.patch_js(once), once)
        self.assertEqual(once.count(patcher.JS_MARKER), 1)
        self.assertIn("renderVideoConsumption(state.data.video_consumption);", once)


if __name__ == "__main__":
    unittest.main()
