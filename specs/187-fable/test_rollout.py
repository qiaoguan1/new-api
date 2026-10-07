"""Offline safety contracts for the single-model Fable rollout."""
import copy
from contextlib import redirect_stdout
import io
import importlib.util
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("fable_rollout", Path(__file__).with_name("rollout.py"))
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)


def plan():
    return {
        "model": "claude-fable-5-1", "group": "文", "group_ratio": "0.15", "compatibility_verified": True,
        "billing_mode": "tiered_expr", "billing_expr": 'tier("base", p * 6 + c * 30 + cr * .6 + cc * 7.5 + cc1h * 12)',
        "options": {"ModelRatio": 3, "CompletionRatio": 5, "CacheRatio": .1, "CreateCacheRatio": 1.25},
        "providers": [{"source": "rolldek-ccmax", "priority": 10}, {"source": "maolao", "priority": 8}],
    }


class SafetyTests(unittest.TestCase):
    def test_validation_mode_is_explicit_canary_only_and_never_mutates_plan(self):
        candidate = {**plan(), "compatibility_verified": False}
        instance = rollout.Rollout(db=rollout.CANARY_DB)
        instance.read = lambda name: candidate
        self.assertIs(instance.load_validation_plan()["compatibility_verified"], False)
        self.assertIs(candidate["compatibility_verified"], False)
        with self.assertRaises(RuntimeError):
            instance.load_plan()
        production = rollout.Rollout(db="new-api")
        for method in ["load_validation_plan", "prepare_validation", "activate_validation"]:
            with self.subTest(method=method), self.assertRaisesRegex(RuntimeError, "canary-only"):
                getattr(production, method)()
            self.assertIsNone(production._helper)
        for method in ["_stage", "_prepare", "_activate"]:
            with self.subTest(method=method), self.assertRaisesRegex(RuntimeError, "canary-only"):
                getattr(production, method)(validation=True)
            self.assertIsNone(production._helper)

    def test_runtime_image_requires_known_baseline_or_exact_attested_candidate(self):
        known = {"Image": rollout.BASELINE_IMAGE, "Config": {"Image": rollout.IMAGE_TAG}, "State": {"Running": True}}
        rollout.validate_runtime_image(known)
        manifest = {"image": "sha256:" + "a" * 64, "tag": rollout.CANDIDATE_TAG}
        candidate = {**known, "Image": manifest["image"], "Config": {"Image": rollout.CANDIDATE_TAG}}
        rollout.validate_runtime_image(candidate, manifest)
        for wrong in [{**known, "Image": "sha256:" + "b" * 64},
                      {**candidate, "Config": {"Image": "untrusted:latest"}},
                      {**candidate, "Image": "sha256:" + "c" * 64}]:
            with self.assertRaises(RuntimeError):
                rollout.validate_runtime_image(wrong, manifest)

    def test_candidate_manifest_binds_baseline_image_and_current_source_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory) / "private"
            source_root = Path(directory) / "source"
            baseline_root = Path(directory) / "baseline"
            source_root.mkdir()
            baseline_root.mkdir()
            (baseline_root / "candidate.go").write_text("baseline source", encoding="utf-8")
            source_path = source_root / "candidate.go"
            source_path.write_text("verified candidate source", encoding="utf-8")
            current_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            manifest = {"image": "sha256:" + "a" * 64, "tag": rollout.CANDIDATE_TAG,
                        "baseline_image": rollout.BASELINE_IMAGE, "source_sha256": {"candidate.go": current_hash},
                        "source_proof": "runtime-source-proof.json"}
            proof = {"image": manifest["image"], "baseline_image": rollout.BASELINE_IMAGE,
                     "source_sha256": manifest["source_sha256"],
                     "baseline_source_sha256": {"candidate.go": hashlib.sha256((baseline_root / "candidate.go").read_bytes()).hexdigest()},
                     "unchanged_source_count": 10, "new_files": []}
            rollout.private_write(private_root / "runtime-source-proof.json", proof)
            manifest["source_proof_sha256"] = hashlib.sha256((private_root / "runtime-source-proof.json").read_bytes()).hexdigest()
            rollout.private_write(private_root / "runtime-candidate.json", manifest)
            instance = rollout.Rollout(db=rollout.CANARY_DB, root=private_root, source_root=source_root, baseline_source_root=baseline_root)
            self.assertEqual(instance.load_candidate()["image"], manifest["image"])
            source_path.write_text("tampered source", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "candidate source changed"):
                instance.load_candidate()

    def test_candidate_manifest_rejects_unknown_baseline_and_path_escape(self):
        base = {"image": "sha256:" + "a" * 64, "tag": rollout.CANDIDATE_TAG,
                "baseline_image": rollout.BASELINE_IMAGE, "source_sha256": {"candidate.go": "b" * 64},
                "source_proof": "runtime-source-proof.json", "source_proof_sha256": "c" * 64}
        rollout.validate_candidate_manifest(base)
        for changed in [{"baseline_image": "sha256:" + "d" * 64}, {"image": "new-api-fixed:latest"},
                        {"tag": "untrusted:latest"}, {"source_sha256": {"../secret": "b" * 64}},
                        {"source_proof": "../runtime-source-proof.json"}, {"source_proof_sha256": ""}]:
            with self.assertRaises(RuntimeError):
                rollout.validate_candidate_manifest({**base, **changed})

    def test_new_source_proof_requires_null_hash_list_membership_and_real_baseline_absence(self):
        for defect in [None, "not_listed", "baseline_exists"]:
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                private_root = Path(directory) / "private"
                source_root, baseline_root = Path(directory) / "source", Path(directory) / "baseline"
                source_root.mkdir()
                baseline_root.mkdir()
                (source_root / "new.go").write_text("new file", encoding="utf-8")
                digest = hashlib.sha256((source_root / "new.go").read_bytes()).hexdigest()
                manifest = {"image": "sha256:" + "a" * 64, "tag": rollout.CANDIDATE_TAG,
                            "baseline_image": rollout.BASELINE_IMAGE, "source_sha256": {"new.go": digest},
                            "source_proof": "runtime-source-proof.json"}
                proof = {"image": manifest["image"], "baseline_image": rollout.BASELINE_IMAGE,
                         "source_sha256": manifest["source_sha256"], "baseline_source_sha256": {"new.go": None},
                         "unchanged_source_count": 10, "new_files": [] if defect == "not_listed" else ["new.go"]}
                if defect == "baseline_exists":
                    (baseline_root / "new.go").write_text("already exists", encoding="utf-8")
                rollout.private_write(private_root / "runtime-source-proof.json", proof)
                manifest["source_proof_sha256"] = hashlib.sha256((private_root / "runtime-source-proof.json").read_bytes()).hexdigest()
                rollout.private_write(private_root / "runtime-candidate.json", manifest)
                instance = rollout.Rollout(db=rollout.CANARY_DB, root=private_root,
                                           source_root=source_root, baseline_source_root=baseline_root)
                if defect is None:
                    self.assertEqual(instance.load_candidate()["image"], manifest["image"])
                else:
                    with self.assertRaises(RuntimeError):
                        instance.load_candidate()

    def test_validation_stage_records_candidate_and_scope_without_promoting_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            false_plan = {**plan(), "compatibility_verified": False}
            instance = rollout.Rollout(db=rollout.CANARY_DB, root=Path(directory), helper=Mock())
            instance.read = lambda name: false_plan if name == "plan.json" else {
                "token_id": 17, "key": "sk-private-test-only", "group": "ccmax" if name.startswith("rolldek") else "group_4",
                "api_origin": "https://rolldek.com" if name.startswith("rolldek") else "https://api.maolaoapi.com"}
            candidate_image = "sha256:" + "a" * 64
            instance.load_candidate = lambda: {"image": candidate_image}
            instance.image = lambda: {"Image": rollout.BASELINE_IMAGE}
            instance.h.env.return_value = {"SQL_DSN": "postgres://db/new-api"}
            instance.options = lambda: {"GroupRatio": '{"文":0.15}', **{key: '{"other":1}' for key in rollout.RATIO_KEYS}}
            routes = [{"id": index + 100, "name": "Claude187:" + source + ":" + rollout.MODEL,
                       "type": 14, "key": "sk-private-test-only", "base_url": url, "models": rollout.MODEL,
                       "group": "文", "priority": priority, "weight": 0, "model_mapping": "",
                       "setting": '{"pass_through_body_enabled":false}', "status": 2}
                      for index, (source, url, priority) in enumerate([
                          ("rolldek-ccmax", "https://rolldek.com", 10), ("maolao", "https://api.maolaoapi.com", 8)])]
            abilities = [{"channel_id": row["id"], "model": rollout.MODEL, "group": "文", "priority": row["priority"],
                          "weight": 0, "enabled": False} for row in routes]
            instance.snapshot_routes = Mock(side_effect=[([], [], []), (routes, abilities, [{"id": 7, "model_name": rollout.MODEL, "status": 0}])])
            instance.channel_sequence_snapshot = lambda: {"sequence": "public.channels_id_seq", "last_value": 68, "is_called": True}
            instance.sql = Mock(return_value="BEGIN\n" + json.dumps(routes) + "\nCOMMIT")
            with redirect_stdout(io.StringIO()):
                instance._stage(validation=True)
            staged = json.loads(instance.phase("stage").read_text(encoding="utf-8"))
            before = json.loads(instance.phase("before").read_text(encoding="utf-8"))
            self.assertEqual(staged["image"], candidate_image)
            self.assertEqual(staged["production_image"], rollout.BASELINE_IMAGE)
            self.assertEqual(before["baseline_image"], rollout.BASELINE_IMAGE)
            self.assertEqual(before["channels_sequence_before"], {"sequence": "public.channels_id_seq", "last_value": 68, "is_called": True})
            self.assertIs(staged["validation_only"], True)
            self.assertIs(false_plan["compatibility_verified"], False)
            statements = instance.sql.call_args.args[0]
            self.assertNotIn("UPDATE users", statements)
            self.assertNotIn("UPDATE tokens", statements)
            self.assertIn("id,false", statements)
            # Both allocations occur after the writer-conflicting table lock;
            # each statement sees the preceding insert, not a stale sequence.
            channel_insert = "INSERT INTO channels(id,type,key"
            allocation = "(SELECT coalesce(max(id),0)+1 FROM channels)"
            self.assertEqual(statements.count(channel_insert), 2)
            self.assertEqual(statements.count(allocation), 2)
            self.assertLess(statements.index("LOCK TABLE options,channels,abilities,models IN SHARE ROW EXCLUSIVE MODE"),
                            statements.index(channel_insert))
            self.assertEqual(statements.lower().count("setval("), 1)
            self.assertLess(statements.rindex(channel_insert), statements.index("PERFORM setval"))
            self.assertNotIn("nextval", statements.lower())
            instance.read = lambda name: false_plan if name == "plan.json" else json.loads((Path(directory) / name).read_text(encoding="utf-8"))
            metadata = staged["metadata"]
            active_routes = [{**row, "status": 1} for row in routes]
            active_abilities = [{**row, "enabled": True} for row in abilities]
            instance.snapshot_routes = Mock(side_effect=[(routes, abilities, metadata),
                                                         (active_routes, active_abilities, [{**metadata[0], "status": 1}])])
            instance.options = lambda: {"GroupRatio": '{"文":0.15}', **{key: json.dumps({rollout.MODEL: value}) for key, value in staged["updates"].items()}}
            instance.runtime_gate = Mock(return_value={"image": candidate_image, "model": rollout.MODEL})
            with redirect_stdout(io.StringIO()):
                instance.activate_validation()
            self.assertTrue(instance.phase("activate-validation").exists())
            self.assertFalse(instance.phase("activate").exists())
            self.assertIs(false_plan["compatibility_verified"], False)
            activation_sql = instance.sql.call_args.args[0]
            self.assertIn("UPDATE channels SET status=1 WHERE id IN (100,101)", activation_sql)
            self.assertNotIn("UPDATE users", activation_sql)
            self.assertNotIn("UPDATE tokens", activation_sql)
            with self.assertRaisesRegex(RuntimeError, "compatibility is unverified"):
                instance.activate()

    def test_validation_runtime_rejects_environment_drift_before_admin_or_http(self):
        instance = rollout.Rollout(db=rollout.CANARY_DB, helper=Mock())
        candidate_image = "sha256:" + "a" * 64
        values = rollout.canary_environment({"SQL_DSN": "postgres://db/new-api"})
        saved = {"validation_only": True, "image": candidate_image, "production_image": rollout.BASELINE_IMAGE,
                 "dsn_sha256": hashlib.sha256(values["SQL_DSN"].encode()).hexdigest()}
        instance.image = lambda: {"Image": rollout.BASELINE_IMAGE}
        instance.load_candidate = lambda: {"image": candidate_image}
        instance.h.inspect.return_value = {"State": {"Running": True}, "Image": candidate_image}
        instance.rows = Mock(side_effect=AssertionError("no admin read before isolation gate"))
        for key, changed in [("LOG_SQL_DSN", "postgres://db/production"), ("QUOTA_DB_AUTHORITATIVE", "false"),
                             ("BATCH_UPDATE_ENABLED", "true"), ("SQL_MAX_OPEN_CONNS", "30")]:
            instance.h.env.return_value = {**values, key: changed}
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "canary external state isolation"):
                instance.runtime_gate(saved)
        instance.rows.assert_not_called()

    def test_stage_allocator_uses_current_sequence_max_and_never_rewinds_high_values(self):
        statement = rollout.advance_channel_sequence_sql("public.channels_id_seq")
        self.assertIn("GREATEST(current_value,new_max)", statement)
        self.assertIn("pg_get_serial_sequence(''channels'',''id'')", statement)
        self.assertIn("sequence_name::regclass IS DISTINCT FROM ''public.channels_id_seq''::regclass", statement)
        self.assertIn("format(''SELECT last_value FROM %s'',sequence_oid)", statement)
        self.assertIn("name LIKE ''Claude187:%'' AND models=''claude-fable-5-1''", statement)
        self.assertNotIn("models_id_seq", statement)
        instance = rollout.Rollout(db=rollout.CANARY_DB)
        instance.rows = Mock(side_effect=[[{"sequence": "public.channels_id_seq"}], [{"last_value": 200, "is_called": False}]])
        snapshot = instance.channel_sequence_snapshot()
        self.assertEqual(snapshot, {"sequence": "public.channels_id_seq", "last_value": 200, "is_called": False})

    def test_production_key_requires_fresh_exact_long_lived_scope_proof(self):
        secret = {"token_id": 17, "key": "sk-private-test-only", "group": "ccmax"}
        proof = {"token_id": 17, "key_sha256": hashlib.sha256(secret["key"].encode()).hexdigest(),
                 "group": "ccmax", "model": rollout.MODEL, "allow_ips": "156.239.3.210", "status": 1,
                 "expired_time": -1, "unlimited_quota": True, "cross_group_retry": False, "verified_at": 1000}
        rollout.verify_production_key("rolldek-ccmax", secret, proof, now=1300)
        for field, value in [("token_id", 18), ("key_sha256", "wrong"), ("group", "trial"),
                             ("model", "claude-fable-5"), ("allow_ips", ""), ("status", 2),
                             ("expired_time", 9999), ("unlimited_quota", False), ("cross_group_retry", True),
                             ("verified_at", 999), ("verified_at", 1301), ("unlimited_quota", "true")]:
            invalid = {**proof, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                rollout.verify_production_key("rolldek-ccmax", secret, invalid, now=1300)
        with tempfile.TemporaryDirectory() as directory:
            instance = rollout.Rollout(root=Path(directory))
            instance.load_plan = lambda: plan()
            instance.read = lambda name: {**secret, "api_origin": "https://rolldek.com"}
            with self.assertRaisesRegex(RuntimeError, "production key proof missing"):
                instance.stage()
            self.assertIsNone(instance._helper)
            self.assertFalse(instance.phase("stage-started").exists())

    def test_stage_rejects_missing_ratio_maps_before_writes_but_expr_maps_may_be_absent(self):
        updates = rollout.option_updates(plan())
        complete = {key: json.dumps({"existing-model": 1}) for key in rollout.RATIO_KEYS}
        rollout.require_ratio_maps(complete)
        merged = rollout.merge_options(complete, updates)
        self.assertEqual(json.loads(merged[rollout.MODE_KEY])[rollout.MODEL], "tiered_expr")
        for missing in rollout.RATIO_KEYS:
            with tempfile.TemporaryDirectory() as directory:
                instance = rollout.Rollout(db=rollout.CANARY_DB, root=Path(directory), helper=Mock())
                instance.load_plan = lambda: plan()
                instance.read = lambda name: {"token_id": 17, "key": "sk-private-test-only",
                                             "group": "ccmax" if name.startswith("rolldek") else "group_4",
                                             "api_origin": "https://rolldek.com" if name.startswith("rolldek") else "https://api.maolaoapi.com"}
                instance.image = lambda: {"Image": "sha256:test"}
                instance.h.env.return_value = {"SQL_DSN": "postgres://db/new-api"}
                instance.options = lambda: {key: value for key, value in complete.items() if key != missing}
                instance.sql = Mock(side_effect=AssertionError("SQL writes must not run"))
                with self.subTest(missing=missing), self.assertRaisesRegex(RuntimeError, "ratio map missing"):
                    instance.stage()
                instance.sql.assert_not_called()
                self.assertFalse(instance.phase("stage-started").exists())

    def test_unverified_candidates_block_every_forward_phase_before_remote_access(self):
        for value in [False, None, "true", 1]:
            candidate = plan()
            candidate["compatibility_verified"] = value
            with self.assertRaises(RuntimeError):
                rollout.validate_plan(candidate)
        for phase in ["stage", "prepare", "activate"]:
            instance = rollout.Rollout(db=rollout.CANARY_DB)
            candidate = plan()
            candidate["compatibility_verified"] = False
            instance.read = lambda name: candidate
            with self.assertRaisesRegex(RuntimeError, "compatibility is unverified"):
                getattr(instance, phase)()
            self.assertIsNone(instance._helper)

    def test_canary_does_not_inherit_production_log_database_or_background_polling(self):
        original = {"SQL_DSN": "postgres://test:private@db/new-api", "LOG_SQL_DSN": "postgres://db/production-logs",
                    "REDIS_CONN_STRING": "redis://production", "CHANNEL_UPDATE_FREQUENCY": "60"}
        environment = rollout.canary_environment(original)
        self.assertTrue(environment["SQL_DSN"].endswith("/" + rollout.CANARY_DB))
        self.assertEqual(environment["LOG_SQL_DSN"], "")
        self.assertEqual(environment["REDIS_CONN_STRING"], "")
        self.assertNotIn("CHANNEL_UPDATE_FREQUENCY", environment)
        self.assertEqual(environment["QUOTA_DB_AUTHORITATIVE"], "true")
        self.assertEqual(original["LOG_SQL_DSN"], "postgres://db/production-logs")

    def test_plan_accepts_numeric_and_string_group_ratio(self):
        for value in ["0.15", .15]:
            candidate = plan()
            candidate["group_ratio"] = value
            self.assertEqual(rollout.validate_plan(candidate)["model"], rollout.MODEL)

    def test_wrong_model_mode_group_or_incomplete_tariff_rejected(self):
        invalid = []
        for field, value in [("model", "claude-fable-5"), ("group", "default"),
                             ("group_ratio", 1), ("billing_mode", "expression"), ("billing_expr", "")]:
            item = plan()
            item[field] = value
            invalid.append(item)
        missing = plan()
        del missing["options"]["CacheRatio"]
        invalid.append(missing)
        nonfinite = plan()
        nonfinite["options"]["ModelRatio"] = float("nan")
        invalid.append(nonfinite)
        for item in invalid:
            with self.assertRaises(RuntimeError):
                rollout.validate_plan(item)

    def test_provider_scope_and_priority_cannot_drift(self):
        for mutation in [{"source": "other", "priority": 8}, {"source": "maolao", "priority": 10}]:
            item = plan()
            item["providers"][1] = mutation
            with self.assertRaises(RuntimeError):
                rollout.validate_plan(item)

    def test_merge_changes_only_exact_model_and_preserves_other_entries(self):
        updates = rollout.option_updates(plan())
        current = {key: json.dumps({"other-model": "preserved"}) for key in updates}
        merged = rollout.merge_options(current, updates)
        for key, value in merged.items():
            self.assertEqual(json.loads(value)["other-model"], "preserved")
            self.assertEqual(json.loads(value)[rollout.MODEL], updates[key])

    def test_rollback_preserves_later_other_model_changes_and_prior_entry(self):
        expected = rollout.option_updates(plan())
        before = {key: json.dumps({rollout.MODEL: "old", "old-model": 1}) for key in expected}
        current = {key: json.dumps({rollout.MODEL: value, "old-model": 2, "later-model": 3})
                   for key, value in expected.items()}
        restored = rollout.restore_options(current, before, expected)
        for value in restored.values():
            self.assertEqual(json.loads(value), {rollout.MODEL: "old", "old-model": 2, "later-model": 3})

    def test_rollback_rejects_target_tariff_changed_by_another_actor(self):
        expected = rollout.option_updates(plan())
        current = rollout.merge_options({}, expected)
        current["ModelRatio"] = json.dumps({rollout.MODEL: 999})
        with self.assertRaises(RuntimeError):
            rollout.restore_options(current, {}, expected)

    def test_absent_option_guard_requires_absence_and_existing_guard_exact_value(self):
        absent = rollout.option_guard("CacheRatio", None)
        present = rollout.option_guard("CacheRatio", '{"a":1}')
        self.assertIn("IF EXISTS", absent)
        self.assertIn("IF NOT EXISTS", present)
        self.assertIn("concurrent option", absent)
        self.assertIn("concurrent option", present)

    def test_option_value_dollar_delimiters_remain_data_not_do_block_delimiters(self):
        guarded = rollout.option_guard("billing_setting.billing_expr", '{"other":"$$ arbitrary $guard$ text"}')
        self.assertTrue(guarded.startswith("DO '"))
        self.assertNotIn("DO $$", guarded)
        self.assertIn("$$ arbitrary $guard$ text", guarded)

    def test_wrong_database_rejected_without_loading_remote_helper(self):
        with self.assertRaises(RuntimeError):
            rollout.Rollout(db="postgres")

    def test_private_phase_marker_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "stage-started.json"
            rollout.private_write(marker, {"phase": "stage"})
            with self.assertRaises(FileExistsError):
                rollout.private_write(marker, {"phase": "stage"})
            self.assertEqual(json.loads(marker.read_text())["phase"], "stage")

    def test_immutable_route_identity_rejects_key_format_or_route_drift(self):
        original = {"id": 12, "name": "Claude187:rolldek-ccmax:" + rollout.MODEL,
                    "type": 14, "key": "private-test-key", "base_url": "https://rolldek.com",
                    "models": rollout.MODEL, "group": "文", "priority": 10, "weight": 0,
                    "model_mapping": "", "setting": '{"pass_through_body_enabled":false}', "status": 2}
        ability = {"channel_id": 12, "model": rollout.MODEL, "group": "文",
                   "priority": 10, "weight": 0, "enabled": False}
        rollout.validate_routes([original], [ability], [original], 2)
        for field, value in [("key", "different"), ("type", 1), ("model_mapping", "alias"), ("models", "claude-fable-5")]:
            changed = copy.deepcopy(original)
            changed[field] = value
            with self.assertRaises(RuntimeError):
                rollout.validate_routes([changed], [ability], [original], 2)


if __name__ == "__main__":
    unittest.main()
