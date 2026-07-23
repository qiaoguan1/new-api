import pathlib
import sys
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import channel_name_policy as policy  # noqa: E402


class ChannelNamePolicyTests(unittest.TestCase):
    def test_policy_covers_complete_unique_inventory(self):
        self.assertEqual(len(policy.CHANNEL_NAMES), 35)
        names = [new for _, new in policy.CHANNEL_NAMES.values()]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(name.count(" · ") == 1 for name in names))

    def test_inventory_accepts_legacy_and_is_idempotent_after_apply(self):
        legacy = [
            {"id": channel_id, "name": old}
            for channel_id, (old, _) in policy.CHANNEL_NAMES.items()
        ]
        applied = [
            {"id": channel_id, "name": new}
            for channel_id, (_, new) in policy.CHANNEL_NAMES.items()
        ]
        self.assertEqual(policy.validate_inventory(legacy), "pending")
        self.assertEqual(policy.validate_inventory(applied), "applied")

    def test_inventory_rejects_unknown_or_missing_channels(self):
        rows = [
            {"id": channel_id, "name": old}
            for channel_id, (old, _) in policy.CHANNEL_NAMES.items()
        ]
        with self.assertRaisesRegex(ValueError, "inventory drift"):
            policy.validate_inventory(rows[:-1])
        rows.append({"id": 999, "name": "Unknown"})
        with self.assertRaisesRegex(ValueError, "inventory drift"):
            policy.validate_inventory(rows)

    def test_inventory_rejects_manual_name_drift(self):
        rows = [
            {"id": channel_id, "name": old}
            for channel_id, (old, _) in policy.CHANNEL_NAMES.items()
        ]
        rows[0]["name"] = "surprise"
        with self.assertRaisesRegex(ValueError, "name drift"):
            policy.validate_inventory(rows)

    def test_migration_is_one_transaction_and_updates_only_name(self):
        sql = policy.migration_sql()
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertTrue(sql.endswith("COMMIT;\n"))
        self.assertIn("UPDATE channels c SET name = p.new_name", sql)
        self.assertIn("to_jsonb(c) - 'name'", sql)
        self.assertIn("ON COMMIT DROP", sql)
        self.assertNotIn("SET status", sql)
        self.assertNotIn("SET models", sql)
        self.assertNotIn("SET key", sql)

    def test_retired_providers_remain_in_policy_without_status_mutation(self):
        self.assertEqual(policy.CHANNEL_NAMES[15][1], "PackAPI · 图片")
        self.assertEqual(policy.CHANNEL_NAMES[21][1], "Unity2 · 图片")
        self.assertNotIn("status", policy.migration_sql().lower())


if __name__ == "__main__":
    unittest.main()
