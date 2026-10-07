"""Offline fail-closed promotion of exact-image canary evidence."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import finalize_canary as finalizer
from plan import build_plan
from test_plan import evidence


def fixture_proofs(plan, candidate):
    """Supply all independently required protocol, TTL and retained-route cases."""
    binding = {"model": finalizer.r.MODEL, "image": candidate["image"],
               "source_proof_sha256": candidate["source_proof_sha256"],
               "plan_sha256": finalizer.plan_sha256(plan)}
    fake_results = []
    for path, case in sorted(finalizer.FAKE_CASES):
        params, maximum = finalizer.fake_vector(case)
        fake_results.append({"path": path, "case": case, "http": 200,
                             "quota": finalizer.expected_quota(plan, params, maximum),
                             "billing_exact": True, "upstream_posts": 1})
    fake_results += [{"path": path, "case": "invalid", "http": 400, "quota": 0, "upstream_posts": 0}
                     for path in finalizer.INVALID_PATHS]
    real_results = []
    for source, path, effort in sorted(finalizer.REAL_CASES):
        params = {"p": 20, "c": 8, "cr": 0, "cc": 0, "cc1h": 0}
        usage = ({"input_tokens": 20, "output_tokens": 8} if path == "/v1/messages" else
                 {"prompt_tokens": 20, "completion_tokens": 8})
        real_results.append({"source": source, "path": path, "effort": effort, "http": 200,
                             "response_model": finalizer.r.MODEL, "usage": usage, "uncertain": False,
                             "billing_exact": True, "tiered_token_params": params,
                             "quota": finalizer.expected_quota(plan, params, effort == "max")})
    return ({**binding, "success": True, "phase": "verified", "production_unchanged": True,
             "started_at": 10, "finished_at": 20},
            {**binding, "billing_exact": True, "compatibility_verified": True, "results": fake_results},
            {**binding, "billing_exact": True, "results": real_results})


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.plan = build_plan([evidence("rolldek-ccmax"), evidence("maolao")])
        self.candidate = {"image": "sha256:" + "a" * 64, "tag": finalizer.r.CANDIDATE_TAG,
                          "baseline_image": finalizer.r.BASELINE_IMAGE,
                          "source_proof": "runtime-source-proof.json", "source_proof_sha256": "b" * 64,
                          "source_sha256": {"candidate.go": "c" * 64}}
        pipeline, fake, real = fixture_proofs(self.plan, self.candidate)
        self.proofs = (pipeline, fake, real)
        self.options = {"GroupRatio": json.dumps({"文": .15}),
                        **{key: json.dumps({finalizer.r.MODEL: value})
                           for key, value in finalizer.r.option_updates(self.plan).items()}}
        for name, value in [("plan.json", self.plan), ("runtime-candidate.json", self.candidate),
                            ("validation-pipeline-result.json", pipeline), ("fake-verified.json", fake),
                            ("real-verified.json", real),
                            (finalizer.r.CANARY_DB + "-stage.json", {
                                "image": self.candidate["image"], "validation_only": True,
                                "plan_sha256": finalizer.plan_sha256(self.plan),
                                "dsn_sha256": hashlib.sha256(("postgres://fixture/" + finalizer.r.CANARY_DB).encode()).hexdigest(),
                                "updates": finalizer.r.option_updates(self.plan)})]:
            finalizer.r.private_write(self.root / name, value)
        self.rollout = Mock(db=finalizer.r.CANARY_DB, root=self.root)
        self.rollout.read.side_effect = lambda name: json.loads((self.root / name).read_text(encoding="utf-8"))
        self.rollout.load_candidate.return_value = self.candidate
        self.rollout.h.inspect.return_value = {"Image": self.candidate["image"], "State": {"Running": True}}
        self.rollout.h.env.return_value = {
            "SQL_DSN": "postgres://fixture/" + finalizer.r.CANARY_DB,
            "QUOTA_DB_AUTHORITATIVE": "true", "BATCH_UPDATE_ENABLED": "false", "NODE_TYPE": "slave",
            "MEMORY_CACHE_ENABLED": "false", "SQL_MAX_OPEN_CONNS": "5", "SQL_MAX_IDLE_CONNS": "1",
            "SQL_MAX_LIFETIME": "60", "LOG_SQL_DSN": "", "REDIS_CONN_STRING": ""}
        self.rollout.h.address.return_value = "http://127.0.0.1:19087"
        self.rollout.rows.return_value = [{"id": 1, "token": "fixture-admin"}]
        self.rollout.options.return_value = self.options
        self.opener = Mock(side_effect=self.open_options)

    def open_options(self, request, timeout):
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "http://127.0.0.1:19087/api/option/")
        return io.BytesIO(json.dumps({"success": True, "data": [
            {"key": key, "value": value} for key, value in self.options.items()]}).encode())

    def test_promotes_only_flag_and_writes_activate_compatible_image_and_plan_binding(self):
        result = finalizer.finalize(self.rollout, self.opener)
        promoted = self.rollout.read("plan.json")
        self.assertEqual(promoted, {**self.plan, "compatibility_verified": True})
        proof = self.rollout.read("canary-verified.json")
        self.assertEqual(proof["image"], self.candidate["image"])
        self.assertEqual(proof["plan_sha256"], finalizer.plan_sha256(promoted))
        for key in ["compatibility_verified", "billing_exact", "runtime_pricing_verified"]:
            self.assertIs(proof[key], True)
        self.assertEqual(result, proof)
        self.assertEqual(self.rollout.read("plan-before-canary-verification.json"), self.plan)
        self.assertEqual(set(proof["evidence_sha256"]), {
            "validation-pipeline-result.json", "fake-verified.json", "real-verified.json"})
        self.rollout.sql.assert_not_called()
        self.rollout.h.inspect.assert_called_with(finalizer.r.CANARY_NAME)
        self.rollout.validation_scope.assert_called_with(True)

    def test_rejects_each_missing_binding_without_promoting_plan(self):
        for proof_index in range(3):
            for key in ["image", "source_proof_sha256", "plan_sha256"]:
                with self.subTest(proof=proof_index, key=key):
                    changed = copy.deepcopy(self.proofs)
                    changed[proof_index].pop(key)
                    with self.assertRaises(finalizer.r.RolloutError):
                        finalizer.validate_proofs(self.plan, self.candidate, *changed)
        self.assertIs(self.rollout.read("plan.json")["compatibility_verified"], False)

    def test_rejects_failed_or_stale_pipeline_and_wrong_exact_model(self):
        for change in [{"success": False}, {"production_unchanged": False}, {"phase": "verify_real.py"},
                       {"image": "sha256:" + "d" * 64}, {"source_proof_sha256": "d" * 64},
                       {"plan_sha256": "d" * 64}, {"finished_at": 9}]:
            with self.subTest(change=change), self.assertRaises(finalizer.r.RolloutError):
                finalizer.validate_proofs(self.plan, self.candidate, {**self.proofs[0], **change}, *self.proofs[1:])
        changed = copy.deepcopy(self.proofs)
        changed[2]["results"][0]["response_model"] = "claude-fable-5"
        with self.assertRaises(finalizer.r.RolloutError):
            finalizer.validate_proofs(self.plan, self.candidate, *changed)

    def test_rejects_wrong_fake_real_vectors_and_incomplete_cases(self):
        for proof_index, mutation in [(1, "quota"), (2, "quota"), (2, "usage"), (1, "missing"), (2, "missing")]:
            with self.subTest(proof=proof_index, mutation=mutation):
                changed = copy.deepcopy(self.proofs)
                if mutation == "missing":
                    changed[proof_index]["results"].pop()
                elif mutation == "usage":
                    usage = changed[proof_index]["results"][0]["usage"]
                    usage["input_tokens" if "input_tokens" in usage else "prompt_tokens"] += 1
                else:
                    changed[proof_index]["results"][0]["quota"] += 1
                with self.assertRaises(finalizer.r.RolloutError):
                    finalizer.validate_proofs(self.plan, self.candidate, *changed)

    def test_rejects_paid_or_posted_invalid_request(self):
        for key, value in [("http", 500), ("quota", 1), ("upstream_posts", 1)]:
            changed = copy.deepcopy(self.proofs)
            changed[1]["results"][-1][key] = value
            with self.subTest(key=key), self.assertRaises(finalizer.r.RolloutError):
                finalizer.validate_proofs(self.plan, self.candidate, *changed)

    def test_runtime_price_or_database_drift_fails_before_any_file_promotion(self):
        self.options[finalizer.r.EXPR_KEY] = json.dumps({finalizer.r.MODEL: 'tier("bad",p)'})
        with self.assertRaises(finalizer.r.RolloutError):
            finalizer.finalize(self.rollout, self.opener)
        self.assertIs(self.rollout.read("plan.json")["compatibility_verified"], False)
        self.assertFalse((self.root / "canary-verified.json").exists())
        self.assertFalse((self.root / "finalize-canary-started.json").exists())

    def test_wrong_canary_image_or_production_database_is_rejected(self):
        for defect in ["image", "db"]:
            with self.subTest(defect=defect):
                if defect == "image":
                    self.rollout.h.inspect.return_value["Image"] = finalizer.r.BASELINE_IMAGE
                else:
                    self.rollout.h.inspect.return_value["Image"] = self.candidate["image"]
                    self.rollout.h.env.return_value["SQL_DSN"] = "postgres://fixture/new-api"
                with self.assertRaises(finalizer.r.RolloutError):
                    finalizer.finalize(self.rollout, self.opener)
                self.assertFalse((self.root / "canary-verified.json").exists())

    def test_existing_finalize_marker_is_not_replayed(self):
        finalizer.r.private_write(self.root / "finalize-canary-started.json", {"uncertain": True})
        with self.assertRaises(finalizer.r.RolloutError):
            finalizer.finalize(self.rollout, self.opener)
        self.assertIs(self.rollout.read("plan.json")["compatibility_verified"], False)

    def test_same_named_database_at_changed_authority_is_rejected(self):
        self.rollout.h.env.return_value["SQL_DSN"] = "postgres://different-host/" + finalizer.r.CANARY_DB
        with self.assertRaises(finalizer.r.RolloutError):
            finalizer.finalize(self.rollout, self.opener)
        self.assertFalse((self.root / "canary-verified.json").exists())

    def test_proof_changed_between_validation_and_hash_cannot_be_bound(self):
        def read_then_change(name):
            value = json.loads((self.root / name).read_text(encoding="utf-8"))
            if name == "real-verified.json":
                changed = copy.deepcopy(value)
                changed["results"][0]["quota"] += 1
                (self.root / name).write_text(json.dumps(changed), encoding="utf-8")
            return value
        self.rollout.read.side_effect = read_then_change
        with self.assertRaises(finalizer.r.RolloutError):
            finalizer.finalize(self.rollout, self.opener)
        self.assertFalse((self.root / "canary-verified.json").exists())

    def test_atomic_replace_does_not_overwrite_a_concurrently_edited_plan(self):
        old_digest = hashlib.sha256((self.root / "plan.json").read_bytes()).hexdigest()
        changed = {**self.plan, "deployment_blocker": "concurrent user edit"}
        (self.root / "plan.json").write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(finalizer.r.RolloutError):
            finalizer.replace_plan(self.root / "plan.json", {**self.plan, "compatibility_verified": True}, old_digest)
        self.assertEqual(self.rollout.read("plan.json"), changed)
        self.assertFalse(list(self.root.glob(".plan-finalize-*")))


if __name__ == "__main__":
    unittest.main()
