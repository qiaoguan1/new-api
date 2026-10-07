"""Offline contracts for fresh retained-source cost evidence; no networking."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location("cost_recheck", Path(__file__).with_name("cost_recheck.py"))
cost = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cost)


def pricing(source="rolldek-ccmax"):
    group = cost.SOURCES[source][2]
    return {"success": True, "data": [{"model_name": cost.MODEL, "enable_groups": [group],
            "billing_mode": "tiered_expr", "billing_expr": 'tier("base",p*10+c*50+cr*.25+cc*12.5+cc1h*20)',
            "model_ratio": 5, "completion_ratio": 5, "cache_ratio": .025,
            "create_cache_ratio": 1.25, "model_price": 0, "quota_type": 0}],
            "group_ratio": {group: 1.2 if source == "rolldek-ccmax" else 1.3},
            "usable_group": {group: "enabled"}}


def secret(source):
    origin, api_origin, group = cost.SOURCES[source]
    return {"origin": origin, "api_origin": api_origin, "group": group,
            "cny_per_credit": 1 if source == "rolldek-ccmax" else 1.03,
            "key": "must-not-be-exposed"}


class CostRecheckTests(unittest.TestCase):
    def test_matching_contract_normalizes_numeric_types_not_expressions(self):
        saved, live = pricing(), pricing()
        live["data"][0]["model_ratio"] = "5.000"
        live["group_ratio"]["ccmax"] = "1.200"
        result = cost.compare_contract("rolldek-ccmax", saved, live, secret("rolldek-ccmax"), {"rate": "1"})
        self.assertEqual(result["group_ratio"], "1.2")
        self.assertEqual(result["baseline_contract_sha256"], result["live_contract_sha256"])
        self.assertNotIn("must-not-be-exposed", json.dumps(result))

    def test_every_known_price_field_changes_fail(self):
        for field in ("model_ratio", "completion_ratio", "cache_ratio", "create_cache_ratio", "model_price", "quota_type"):
            with self.subTest(field=field):
                saved, live = pricing(), pricing()
                live["data"][0][field] += 1
                with self.assertRaises(cost.CostError):
                    cost.compare_contract("rolldek-ccmax", saved, live, secret("rolldek-ccmax"), {"rate": 1})

    def test_missing_added_unsupported_and_ambiguous_cost_fields_fail(self):
        for change in ("missing", "added", "unsupported", "duplicate"):
            with self.subTest(change=change):
                saved, live = pricing(), pricing()
                row = live["data"][0]
                if change == "missing":
                    row.pop("cache_ratio")
                elif change == "added":
                    row["audio_ratio"] = 1
                elif change == "unsupported":
                    row["unreviewed_cost_multiplier"] = 2
                else:
                    live["data"].append(dict(row))
                with self.assertRaises(cost.CostError):
                    cost.compare_contract("rolldek-ccmax", saved, live, secret("rolldek-ccmax"), {"rate": 1})

    def test_expression_mode_group_and_conversion_drift_fail(self):
        for field in ("expr", "mode", "ratio", "group", "conversion", "origin"):
            with self.subTest(field=field):
                saved, live, key, credential = pricing(), pricing(), secret("rolldek-ccmax"), {"rate": 1}
                if field == "expr":
                    live["data"][0]["billing_expr"] += "*3"
                elif field == "mode":
                    live["data"][0]["billing_mode"] = "ratio"
                elif field == "ratio":
                    live["group_ratio"]["ccmax"] = 2
                elif field == "group":
                    live["data"][0]["enable_groups"] = ["wrong"]
                elif field == "conversion":
                    credential["rate"] = 2
                else:
                    key["origin"] = "https://wrong.invalid"
                with self.assertRaises(cost.CostError):
                    cost.compare_contract("rolldek-ccmax", saved, live, key, credential)

    def test_absent_empty_nonfinite_and_boolean_numbers_fail(self):
        for value in (None, "", float("nan"), float("inf"), True, -1):
            live = pricing()
            live["data"][0]["cache_ratio"] = value
            with self.subTest(value=value), self.assertRaises(cost.CostError):
                cost.compare_contract("rolldek-ccmax", pricing(), live, secret("rolldek-ccmax"), {"rate": 1})
        saved = pricing()
        saved["data"][0].pop("billing_expr")
        with self.assertRaises(cost.CostError):
            cost.project_contract(saved, "rolldek-ccmax")

    def fixture(self, root, failure=None):
        plan = {"model": cost.MODEL, "compatibility_verified": True,
                "providers": [{"source": "rolldek-ccmax", "priority": 10}, {"source": "maolao", "priority": 8}]}
        candidate = {"image": "sha256:" + "a" * 64, "source_proof_sha256": "b" * 64}
        final = {"model": cost.MODEL, "image": candidate["image"], "source_proof_sha256": candidate["source_proof_sha256"],
                 "plan_sha256": cost.digest(plan), "compatibility_verified": True,
                 "billing_exact": True, "runtime_pricing_verified": True}
        values = {"canary-verified.json": final}
        for source in cost.SOURCES:
            values[source + "-secret.json"] = secret(source)
            values[source + "-pricing.json"] = pricing(source)
        rollout = Mock()
        rollout.read.side_effect = lambda name: copy.deepcopy(values[name])
        rollout.load_plan.return_value, rollout.load_candidate.return_value = plan, candidate
        credentials = {"rolldek": {"rate": 1}, "maolao": {"rate": 1.03}}
        def fetch(source, credential):
            if source == failure:
                raise RuntimeError("password cookie secret must not escape")
            return pricing(source)
        fetch = Mock(side_effect=fetch)
        writer = Mock()
        return cost.CostRecheck(rollout, credentials, fetch, Path(root), writer), fetch, writer, values

    def test_both_sources_success_binds_true_plan_candidate_and_private_proof(self):
        with tempfile.TemporaryDirectory() as root:
            checker, fetch, writer, _ = self.fixture(root)
            report = checker.run()
            self.assertTrue(report["success"])
            self.assertTrue(report["cost_contract_verified"])
            self.assertEqual(len(report["sources"]), 2)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(writer.call_args.args[0].name, "freshcost-proof.json")
            self.assertEqual(report["generation_posts"], 0)
            self.assertEqual(report["price_mutations"], 0)
            self.assertNotIn("must-not-be-exposed", json.dumps(report))

    def test_fresh_proof_rejects_stale_incomplete_or_unbound_source_fields(self):
        with tempfile.TemporaryDirectory() as root:
            checker, _, _, _ = self.fixture(root)
            proof = checker.run()
            plan, candidate = checker.rollout.load_plan(), checker.rollout.load_candidate()
            now = proof["verified_at"]
            cost.validate_fresh_cost_proof(proof, plan, candidate, now)
            for change in ("stale", "future", "coverage", "contract", "evidence"):
                tampered = copy.deepcopy(proof)
                if change == "stale":
                    tampered["sources"][0]["verified_at"] = now - 301
                elif change == "future":
                    tampered["verified_at"] = now + 1
                elif change == "coverage":
                    tampered["sources"].pop()
                elif change == "contract":
                    tampered["sources"][0]["cost_fields"]["model_price"] = "999"
                else:
                    tampered["sources"][0]["live_evidence_sha256"] = "missing"
                with self.subTest(change=change), self.assertRaises(cost.CostError):
                    cost.validate_fresh_cost_proof(tampered, plan, candidate, now)

    def test_one_failure_does_not_skip_other_or_write_success_proof(self):
        with tempfile.TemporaryDirectory() as root:
            checker, fetch, writer, _ = self.fixture(root, "rolldek-ccmax")
            report = checker.run()
            self.assertFalse(report["success"])
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(len(report["sources"]), 1)
            self.assertEqual(report["failures"][0]["source"], "rolldek-ccmax")
            self.assertNotIn("password", json.dumps(report))
            self.assertTrue(writer.call_args.args[0].name.startswith("cost-recheck-failed-"))

    def test_incomplete_final_proof_stops_before_sources(self):
        with tempfile.TemporaryDirectory() as root:
            checker, fetch, writer, values = self.fixture(root)
            values["canary-verified.json"]["plan_sha256"] = "c" * 64
            report = checker.run()
            self.assertFalse(report["success"])
            fetch.assert_not_called()
            writer.assert_called_once()

    def test_existing_proof_fails_closed_before_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            checker, fetch, writer, _ = self.fixture(root)
            (Path(root) / "freshcost-proof.json").write_text("old")
            report = checker.run()
            self.assertFalse(report["success"])
            fetch.assert_not_called()
            self.assertEqual((Path(root) / "freshcost-proof.json").read_text(), "old")

    def test_get_transport_never_redirects_or_submits_generation(self):
        helper, session = Mock(), Mock()
        response = session.get.return_value
        response.status_code, response.json.return_value = 200, pricing()
        result = cost.fetch_pricing("rolldek-ccmax", {"username": "account", "password": "private"}, helper, lambda: session)
        self.assertEqual(result, pricing())
        session.get.assert_called_once_with("https://rolldek.com/api/pricing", timeout=20, allow_redirects=False)
        session.post.assert_not_called()
        session.put.assert_not_called()
        helper.standard_logout.assert_called_once()
        session.close.assert_called_once()

    def test_redirect_transport_rejected_and_session_always_closed(self):
        helper, session = Mock(), Mock()
        session.get.return_value.status_code = 302
        with self.assertRaises(cost.CostError):
            cost.fetch_pricing("maolao", {"username": "account", "password": "private"}, helper, lambda: session)
        session.get.return_value.json.assert_not_called()
        helper.standard_logout.assert_called_once()
        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
