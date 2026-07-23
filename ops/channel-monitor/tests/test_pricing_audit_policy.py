import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "pricing_audit_policy.py"
SPEC = importlib.util.spec_from_file_location("pricing_audit_policy", MODULE_PATH)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(policy)


def row():
    return {
        "channel_id": 41,
        "name": "internal channel",
        "group": "text",
        "models": {
            "gpt-5.6-sol": {
                "available": True,
                "base_input_usd_per_m": 5.0,
                "base_output_usd_per_m": 30.0,
            }
        },
    }


def complete_ledger(input_cost=1.03, output_cost=6.18):
    return {
        "collection_status": "complete",
        "actual_log_complete": True,
        "per_model_real_cost": {
            "gpt-5.6-sol": {
                "kind": "text",
                "input_cost_cny_per_m": input_cost,
                "output_cost_cny_per_m": output_cost,
            }
        },
    }


class ActualCostAlertTests(unittest.TestCase):
    def test_catalog_price_does_not_create_false_critical_alert(self):
        alerts = policy.actual_cost_alerts(
            row(), complete_ledger(), lambda model, group, settings: (1.545, 9.27), {}
        )

        self.assertEqual(alerts, [])

    def test_actual_input_and_output_underpricing_are_critical(self):
        alerts = policy.actual_cost_alerts(
            row(), complete_ledger(), lambda model, group, settings: (1.0, 5.0), {}
        )

        self.assertEqual(
            [alert["type"] for alert in alerts],
            ["price_below_actual_input", "price_below_actual_output"],
        )
        self.assertEqual(alerts[0]["actual_input_cost_cny_per_m"], 1.03)
        self.assertEqual(alerts[1]["actual_output_cost_cny_per_m"], 6.18)
        self.assertTrue(all(alert["severity"] == "critical" for alert in alerts))

    def test_missing_or_incomplete_actual_cost_never_uses_catalog_as_cost(self):
        incomplete = complete_ledger()
        incomplete["actual_log_complete"] = False
        missing_model = complete_ledger()
        missing_model["per_model_real_cost"] = {}

        self.assertEqual(
            policy.actual_cost_alerts(
                row(), incomplete, lambda model, group, settings: (0.1, 0.1), {}
            ),
            [],
        )
        self.assertEqual(
            policy.actual_cost_alerts(
                row(), missing_model, lambda model, group, settings: (0.1, 0.1), {}
            ),
            [],
        )

    def test_invalid_actual_cost_and_missing_sell_price_fail_closed_without_alert(self):
        self.assertEqual(
            policy.actual_cost_alerts(
                row(), complete_ledger(input_cost="bad"), lambda model, group, settings: (1, 1), {}
            ),
            [],
        )

    def test_malformed_channel_model_inventory_is_ignored(self):
        malformed = row()
        malformed["models"] = []

        self.assertEqual(
            policy.actual_cost_alerts(
                malformed,
                complete_ledger(),
                lambda model, group, settings: (0.1, 0.1),
                {},
            ),
            [],
        )
        self.assertEqual(
            policy.actual_cost_alerts(
                row(), complete_ledger(), lambda model, group, settings: (None, None), {}
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
