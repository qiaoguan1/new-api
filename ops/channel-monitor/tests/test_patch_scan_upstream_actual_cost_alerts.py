import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "scripts"
    / "patch_scan_upstream_actual_cost_alerts.py"
)
SPEC = importlib.util.spec_from_file_location("patch_scan_actual_cost_alerts", MODULE_PATH)
patcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(patcher)


SOURCE = '''from channel_audit_policy import (
    configured_model_pairs,
)

def build_snapshot():
    for row in channel_results:
        for model, upstream_price in (row.get("models") or {}).items():
            if not upstream_price.get("base_input_usd_per_m"):
                continue
            sell_input, sell_output = local_sell_price(model, row.get("group") or "", settings)
            if sell_input and sell_input < upstream_price["base_input_usd_per_m"]:
                alerts.append({
                    "type": "price_below_upstream_input",
                    "channel_id": row.get("channel_id"),
                    "channel_name": row.get("name"),
                    "model": model,
                    "sell_input_usd_per_m": sell_input,
                    "upstream_input_usd_per_m": upstream_price["base_input_usd_per_m"],
                    "severity": "critical",
                })
'''


class ScanPatcherTests(unittest.TestCase):
    def test_replaces_catalog_alert_with_actual_cost_policy(self):
        updated = patcher.transform(SOURCE)

        self.assertIn("from pricing_audit_policy import actual_cost_alerts", updated)
        self.assertIn("alerts.extend(", updated)
        self.assertIn("balance_ledger.get(row.get(\"upstream_slug\")", updated)
        self.assertNotIn("sell_input < upstream_price", updated)
        self.assertEqual(patcher.transform(updated), updated)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(RuntimeError):
            patcher.transform("def unrelated():\n    pass\n")


if __name__ == "__main__":
    unittest.main()
