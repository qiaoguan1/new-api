import pathlib
import sys
import unittest


SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import channel_audit_policy as policy


class ChannelAuditPolicyTests(unittest.TestCase):
    def test_configured_model_pairs_resolve_mapping_chain(self):
        channel = {
            "models": "stable,identity",
            "model_mapping": {
                "stable": "provider-alias",
                "provider-alias": "provider-final",
                "identity": "identity",
            },
        }

        self.assertEqual(
            policy.configured_model_pairs(channel),
            [("stable", "provider-final"), ("identity", "identity")],
        )

    def test_configured_model_pairs_reject_cycle(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            policy.configured_model_pairs(
                {
                    "models": "stable",
                    "model_mapping": {"stable": "alias", "alias": "stable"},
                }
            )

    def test_parse_model_mapping_rejects_shapes_go_would_not_map(self):
        for value in ('{"stable": 1}', '{bad json', {" stable": "provider"}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    policy.parse_model_mapping(value)


if __name__ == "__main__":
    unittest.main()
