import pathlib
import sys
import unittest


SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pricing_audit_policy as policy


def complete_ledger(**models):
    return {
        "collection_status": "complete",
        "actual_log_complete": True,
        "per_model_real_cost": models,
    }


class PricingAuditMappingTests(unittest.TestCase):
    def test_actual_cost_alert_uses_final_upstream_model_key(self):
        row = {
            "channel_id": 1,
            "name": "mapped",
            "group": "文",
            "model_mapping": {"gpt-5.5": "provider-gpt-5.5"},
            "models": {
                "gpt-5.5": {"available": True, "upstream_model": "provider-gpt-5.5"}
            },
        }
        alerts = policy.actual_cost_alerts(
            row,
            complete_ledger(
                **{
                    "provider-gpt-5.5": {
                        "kind": "text",
                        "input_cost_cny_per_m": 10.0,
                        "output_cost_cny_per_m": 20.0,
                    }
                }
            ),
            lambda model, group, settings: (1.0, 1.0),
            {},
        )

        self.assertEqual({item["type"] for item in alerts}, {
            "price_below_actual_input",
            "price_below_actual_output",
        })

    def test_mapping_mismatch_is_critical(self):
        row = {
            "channel_id": 1,
            "name": "mapped",
            "group": "文",
            "model_mapping": {"gpt-5.5": "provider-gpt-5.5"},
            "models": {
                "gpt-5.5": {"available": True, "upstream_model": "different-model"}
            },
        }

        alerts = policy.actual_cost_alerts(
            row, complete_ledger(), lambda model, group, settings: (1.0, 1.0), {}
        )

        self.assertEqual(alerts[0]["type"], "model_mapping_mismatch")
        self.assertEqual(alerts[0]["severity"], "critical")

    def test_non_stable_topaz_name_keeps_original_cost_key(self):
        row = {
            "channel_id": 43,
            "name": "Topaz",
            "group": "视频放大",
            "models": {"aaa-9": {"available": True, "upstream_model": "other"}},
        }
        alerts = policy.actual_cost_alerts(
            row,
            complete_ledger(
                **{
                    "aaa-9": {
                        "kind": "text",
                        "input_cost_cny_per_m": 10.0,
                        "output_cost_cny_per_m": 20.0,
                    }
                }
            ),
            lambda model, group, settings: (1.0, 1.0),
            {},
        )

        self.assertEqual(len(alerts), 2)


if __name__ == "__main__":
    unittest.main()
