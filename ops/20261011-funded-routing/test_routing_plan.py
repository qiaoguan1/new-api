"""Fabricated-data tests; no database, network, credentials, or production access."""

import copy
import unittest

import routing_plan as planner


def fixture() -> planner.Snapshot:
    """Return a fresh, credential-free two-channel routing snapshot."""
    result = {
        "channels": [
            {"id": 6, "status": 1, "priority": 8, "weight": 7, "immutable_sha256": "a" * 64},
            {"id": 23, "status": 1, "priority": 10, "weight": 0, "immutable_sha256": "b" * 64},
        ],
        "abilities": [
            {"group": "文", "model": "gpt-test", "channel_id": 6, "enabled": True, "priority": 8, "weight": 7},
            {"group": "图", "model": "disabled-image", "channel_id": 6, "enabled": False, "priority": 8, "weight": 7},
            {"group": "图", "model": "image-test", "channel_id": 23, "enabled": True, "priority": 10, "weight": 0},
        ],
    }
    for row in result["abilities"]:
        row["immutable_sha256"] = "d" * 64
    return result


class RoutingPlanTests(unittest.TestCase):
    def test_plan_preserves_disabled_abilities_weights_and_source(self) -> None:
        source = fixture()
        original = copy.deepcopy(source)
        plan = planner.build_plan(source, [6], [23])
        self.assertEqual(source, original)
        self.assertEqual(plan["before_hash"], planner.snapshot_hash(plan["before"]))
        self.assertEqual(plan["after_hash"], planner.snapshot_hash(plan["after"]))
        self.assertNotEqual(plan["before_hash"], plan["after_hash"])
        channels = {row["id"]: row for row in plan["after"]["channels"]}
        self.assertEqual((channels[6]["status"], channels[6]["priority"], channels[6]["weight"]), (1, 100, 7))
        self.assertEqual((channels[23]["status"], channels[23]["priority"]), (2, 10))
        abilities = {row["model"]: row for row in plan["after"]["abilities"]}
        self.assertFalse(abilities["disabled-image"]["enabled"])
        self.assertEqual(abilities["disabled-image"]["priority"], 8)
        self.assertEqual(abilities["disabled-image"], original["abilities"][1])
        self.assertFalse(abilities["image-test"]["enabled"])

    def test_order_independent_and_noop_are_supported(self) -> None:
        source = fixture()
        reverse = copy.deepcopy(source)
        reverse["channels"].reverse()
        reverse["abilities"].reverse()
        first = planner.build_plan(source, [6], [23])
        self.assertEqual(first, planner.build_plan(reverse, [6], [23]))
        noop = planner.build_plan(first["after"], [6], [23])
        self.assertEqual(noop["before_hash"], noop["after_hash"])
        self.assertNotIn("UPDATE channels SET", planner.render_apply_sql(noop))

    def test_scope_requires_explicit_unique_disjoint_integer_ids(self) -> None:
        for first, depleted in (([True], [23]), ([6], [False]), ([6, 6], [23]), ([6], [6]), ([], []), ([0], [23])):
            with self.subTest(first=first, depleted=depleted), self.assertRaises(ValueError):
                planner.build_plan(fixture(), first, depleted)

    def test_inactive_funded_or_incomplete_or_extra_rows_are_rejected(self) -> None:
        mutations = [
            lambda s: s["channels"][0].update(status=2),
            lambda s: s["channels"].pop(),
            lambda s: s["channels"].append(dict(s["channels"][0], id=72)),
            lambda s: s["channels"].append(dict(s["channels"][0])),
            lambda s: s["abilities"].clear(),
            lambda s: s["abilities"].append(dict(s["abilities"][0])),
            lambda s: s["abilities"].append(dict(s["abilities"][0], channel_id=72)),
            lambda s: s["channels"][0].update(key="unexpected-secret-field"),
            lambda s: s["abilities"][0].update(enabled=1),
            lambda s: s["channels"][0].update(priority=True),
            lambda s: s["channels"][0].update(weight=-1),
            lambda s: s["channels"][0].update(immutable_sha256="not-a-digest"),
            lambda s: s["abilities"][0].update(model="nul\x00model"),
        ]
        for mutation in mutations:
            source = fixture()
            mutation(source)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                planner.build_plan(source, [6], [23])

    def test_72_is_never_inferred_from_23(self) -> None:
        plan = planner.build_plan(fixture(), [6], [23])
        self.assertEqual(plan["first_ids"], [6])
        self.assertEqual(plan["depleted_ids"], [23])
        self.assertEqual({row["id"] for row in plan["after"]["channels"]}, {6, 23})
        sql = planner.render_apply_sql(plan)
        for excluded in (72, 37, 42):
            self.assertNotIn("channel_id = " + str(excluded), sql)
            self.assertNotIn("id IN (" + str(excluded) + ")", sql)

    def test_sql_has_transaction_rowset_and_hash_guards_and_only_minimal_updates(self) -> None:
        plan = planner.build_plan(fixture(), [6], [23])
        sql = planner.render_apply_sql(plan)
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        for expected in ("SET LOCAL lock_timeout", "SET LOCAL statement_timeout", "pg_advisory_xact_lock",
                         "LOCK TABLE abilities IN SHARE ROW EXCLUSIVE MODE", "FOR UPDATE", "RAISE EXCEPTION",
                         plan["before_hash"], plan["after_hash"]):
            self.assertIn(expected, sql)
        updates = [line.strip() for line in sql.splitlines() if line.strip().startswith("UPDATE ")]
        self.assertEqual(len(updates), 4)
        self.assertTrue(all(line.startswith(("UPDATE channels SET priority", "UPDATE channels SET status",
                                            "UPDATE abilities SET priority", "UPDATE abilities SET enabled")) for line in updates))
        self.assertNotIn("DELETE FROM", sql)
        self.assertNotIn("INSERT INTO", sql)
        self.assertNotIn("rowid", sql)

    def test_rollback_guards_after_hash_and_restores_composite_ability_key(self) -> None:
        plan = planner.build_plan(fixture(), [6], [23])
        sql = planner.render_rollback_sql(plan)
        self.assertLess(sql.index(plan["after_hash"]), sql.index("UPDATE "))
        self.assertGreater(sql.rindex(plan["before_hash"]), sql.index("UPDATE "))
        self.assertIn('"group" =', sql)
        self.assertIn("model =", sql)
        self.assertNotIn("DELETE FROM", sql)

    def test_sql_strings_escape_quotes_and_backslashes_without_interpolation(self) -> None:
        source = fixture()
        source["abilities"][0]["group"] = "x'); SELECT danger(); --"
        source["abilities"][0]["model"] = "back\\slash'quote"
        sql = planner.render_rollback_sql(planner.build_plan(source, [6], [23]))
        self.assertIn("E'x''); SELECT danger(); --'", sql)
        self.assertIn("E'back\\\\slash''quote'", sql)

    def test_dollar_body_delimiter_cannot_be_closed_by_an_ability_name(self) -> None:
        source = fixture()
        source["abilities"][0]["model"] = "$funded_routing_0$'; DROP TABLE channels; --"
        sql = planner.render_rollback_sql(planner.build_plan(source, [6], [23]))
        self.assertIn("DO $funded_routing_1$", sql)
        self.assertEqual(sql.count("$funded_routing_1$"), 2)

    def test_nullable_priority_weight_and_explicit_72_scope_are_preserved(self) -> None:
        source = fixture()
        source["channels"][0].update(priority=None, weight=None)
        source["abilities"][0].update(priority=None, weight=None)
        source["channels"].append(dict(source["channels"][1], id=72))
        source["abilities"].append(dict(source["abilities"][2], channel_id=72))
        plan = planner.build_plan(source, [6], [23, 72])
        self.assertIsNone(plan["after"]["channels"][0]["weight"])
        self.assertTrue(all(not row["enabled"] for row in plan["after"]["abilities"] if row["channel_id"] in {23, 72}))
        rollback = planner.render_rollback_sql(plan)
        self.assertIn("priority = NULL", rollback)

    def test_composite_keys_accept_same_model_in_distinct_groups(self) -> None:
        source = fixture()
        source["abilities"][1]["model"] = source["abilities"][0]["model"]
        source["abilities"][1]["enabled"] = True
        plan = planner.build_plan(source, [6], [23])
        self.assertEqual(len(plan["after"]["abilities"]), 3)
        sql = planner.render_rollback_sql(plan)
        self.assertIn('"group" = E\'图\' AND model = E\'gpt-test\'', sql)
        self.assertIn('"group" = E\'文\' AND model = E\'gpt-test\'', sql)

    def test_render_rejects_tampered_manifest(self) -> None:
        for mutate in (
            lambda p: p.update(after_hash="0" * 64),
            lambda p: p["after"]["channels"][0].update(weight=0),
            lambda p: p["after"]["channels"][0].update(status=True),
            lambda p: p.update(first_ids=[6, 72]),
            lambda p: p.update(extra="unexpected"),
        ):
            plan = planner.build_plan(fixture(), [6], [23])
            mutate(plan)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                planner.render_apply_sql(plan)
            with self.assertRaises(ValueError):
                planner.render_rollback_sql(plan)

    def test_immutable_sql_excludes_only_confirmed_volatile_columns(self) -> None:
        sql = planner.immutable_channel_sql("c")
        for field in ("status", "priority", "used_quota", "response_time", "test_time", "balance", "balance_updated_time"):
            self.assertIn("'" + field + "'", sql)
        for field in ("key", "models", "model_mapping", "weight", "settings", "setting", "group", "base_url"):
            self.assertNotIn("'" + field + "'", sql)
        self.assertIn("to_jsonb(c)", sql)
        with self.assertRaises(ValueError):
            planner.immutable_channel_sql("c); DROP TABLE channels; --")

    def test_ability_immutable_fingerprint_protects_tag_and_future_columns(self) -> None:
        sql = planner.immutable_ability_sql("a")
        self.assertIn("to_jsonb(a)", sql)
        self.assertIn("'enabled'", sql)
        self.assertIn("'priority'", sql)
        self.assertNotIn("'tag'", sql)
        source = fixture()
        plan = planner.build_plan(source, [6], [23])
        changed = copy.deepcopy(plan["after"])
        changed["abilities"][0]["immutable_sha256"] = "e" * 64
        self.assertNotEqual(planner.snapshot_hash(changed), plan["after_hash"])


def alignment_fixture() -> tuple[planner.Snapshot, dict[str, object]]:
    """Model alignment with four preserved disabled GPT-6 capability rows."""
    source = fixture()
    source["channels"][0].update(id=48, priority=4, models="gpt-5.5,gpt-6,gpt-5.6-sol")
    source["channels"][1]["models"] = "image-test"
    source["abilities"] = [
        {"group": "文", "model": name, "channel_id": 48, "enabled": True, "priority": 4, "weight": 7}
        for name in ("gpt-5.5", "gpt-5.6-sol")
    ] + [
        {"group": group, "model": "gpt-6", "channel_id": 48, "enabled": False, "priority": 4, "weight": 7}
        for group in ("文", "default", "pro", "test")
    ] + [source["abilities"][2]]
    for row in source["abilities"]:
        row["immutable_sha256"] = "d" * 64
    return source, {
        "id": 48, "before_models": "gpt-5.5,gpt-6,gpt-5.6-sol",
        "after_models": "gpt-5.5,gpt-5.6-sol", "after_immutable_sha256": "c" * 64,
    }


class ModelAlignmentTests(unittest.TestCase):
    def test_alignment_and_priority_share_transaction_and_disabled_rows_are_untouched(self) -> None:
        source, alignment = alignment_fixture()
        plan = planner.build_plan(source, [48], [23], model_alignments=[alignment])
        channel = next(row for row in plan["after"]["channels"] if row["id"] == 48)
        self.assertEqual((channel["models"], channel["priority"], channel["immutable_sha256"]),
                         (alignment["after_models"], 100, alignment["after_immutable_sha256"]))
        disabled_before = [row for row in plan["before"]["abilities"] if not row["enabled"]]
        disabled_after = [row for row in plan["after"]["abilities"] if row["model"] == "gpt-6"]
        self.assertEqual(disabled_before, disabled_after)
        sql = planner.render_apply_sql(plan)
        self.assertEqual(sql.count("BEGIN;"), 1)
        self.assertEqual(sql.count("COMMIT;"), 1)
        self.assertIn("UPDATE channels SET priority = 100, models = E'gpt-5.5,gpt-5.6-sol' WHERE id IN (48);", sql)
        updates = [line for line in sql.splitlines() if line.startswith("UPDATE abilities SET")]
        self.assertFalse(any("E'gpt-6'" in line for line in updates))
        rollback = planner.render_rollback_sql(plan)
        self.assertIn("models = E'gpt-5.5,gpt-6,gpt-5.6-sol'", rollback)
        self.assertLess(rollback.index(plan["after_hash"]), rollback.index("UPDATE "))

    def test_alignment_rejects_add_reorder_active_remove_bad_before_and_hash(self) -> None:
        mutations = [
            lambda a: a.update(id=23),
            lambda a: a.update(before_models="not-current"),
            lambda a: a.update(after_models="gpt-5.5,gpt-5.6-sol,new-model"),
            lambda a: a.update(after_models="gpt-5.6-sol,gpt-5.5"),
            lambda a: a.update(after_models="gpt-6,gpt-5.6-sol"),
            lambda a: a.update(after_models=""),
            lambda a: a.update(after_models=a["before_models"]),
            lambda a: a.update(after_immutable_sha256="a" * 64),
            lambda a: a.update(after_immutable_sha256="invalid"),
            lambda a: a.update(extra="not-authorized"),
        ]
        for mutation in mutations:
            source, alignment = alignment_fixture()
            mutation(alignment)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                planner.build_plan(source, [48], [23], model_alignments=[alignment])

    def test_alignment_requires_complete_disabled_evidence_and_models_projection(self) -> None:
        for mode in ("enabled", "absent", "mixed_models", "no_models", "duplicate"):
            source, alignment = alignment_fixture()
            alignments = [alignment]
            if mode == "enabled":
                source["abilities"][2]["enabled"] = True
            elif mode == "absent":
                source["abilities"] = [row for row in source["abilities"] if row["model"] != "gpt-6"]
            elif mode == "mixed_models":
                source["channels"][1].pop("models")
            elif mode == "no_models":
                for row in source["channels"]:
                    row.pop("models")
            else:
                alignments.append(copy.deepcopy(alignment))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                planner.build_plan(source, [48], [23], model_alignments=alignments)

    def test_shadow_fingerprint_uses_same_protected_expression_and_safe_literal(self) -> None:
        sql = planner.immutable_channel_sql("c", models_override="x'\\model")
        self.assertIn("jsonb_set(to_jsonb(c), '{models}', to_jsonb(E'x''\\\\model'::text))", sql)
        self.assertIn("'used_quota'", sql)
        self.assertNotIn("'key'", sql)
        self.assertIn("sha256", planner.immutable_json_sql("to_jsonb(c)"))


if __name__ == "__main__":
    unittest.main()
