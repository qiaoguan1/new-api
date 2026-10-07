"""Offline access-policy and read-only verification contracts."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("verify_access", Path(__file__).with_name("verify_access.py"))
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


def token(**changes):
    return {"id": 4, "user_id": 2, "username": "ordinary", "status": 1, "expired_time": -1,
            "remain_quota": 100, "unlimited_quota": False, "deleted_at": None, "user_deleted_at": None,
            "user_status": 1, "user_role": 1, "user_group": "default", "token_group": "auto",
            "model_limits_enabled": False, "model_limits": "", "allow_ips": "", "key": "private-test-key", **changes}


POLICY = {"auto_groups": ["图", "文"], "usable_groups": {"auto": "Auto", "文": "Text", "图": "Image"},
          "group_ratios": {"文": .15, "图": .15}, "special_groups": {}}


class AccessTests(unittest.TestCase):
    def fixture(self, directory):
        """Inject only in-memory reads/GETs; no server helpers are executed."""
        verifier = access.AccessVerifier(Path(directory))
        r, module = Mock(), Mock()
        verifier._rollout, verifier._module = r, module
        image = "sha256:" + "a" * 64
        updates = {"ModelRatio": 66.95, "CompletionRatio": 5, "CacheRatio": .025, "CreateCacheRatio": 1.25,
                   access.MODE_KEY: "tiered_expr", access.EXPR_KEY: 'tier("standard", p*133.9+c*669.5)'}
        options = {key: json.dumps({access.MODEL: value}) for key, value in updates.items()}
        options["GroupRatio"] = json.dumps(POLICY["group_ratios"])
        runtime = {**options, "AutoGroups": json.dumps(POLICY["auto_groups"]),
                   "UserUsableGroups": json.dumps(POLICY["usable_groups"])}
        routes = [{"id": 71, "name": "Claude187:rolldek-ccmax:" + access.MODEL, "priority": 10,
                   "type": 14, "models": access.MODEL, "group": "文", "status": 1},
                  {"id": 72, "name": "Claude187:maolao:" + access.MODEL, "priority": 8,
                   "type": 14, "models": access.MODEL, "group": "文", "status": 1}]
        saved = {"channels": routes, "updates": updates, "image": image}
        r.load_plan.return_value = {"model": access.MODEL}
        r.image.return_value = {"Image": image}
        r.load_candidate.return_value = {"image": image}
        r.read.side_effect = lambda name: saved if name == "new-api-stage.json" else {"options": options}
        r.snapshot_routes.return_value = (routes, [], [{"id": 9, "model_name": access.MODEL, "status": 1}])
        r.options.return_value = options
        r.runtime_gate.return_value = {"image": image, "verified_option_keys": list(updates)}
        r.h.address.return_value = "http://127.0.0.1:3000"
        rows = [token(), token(id=5, username="lian123"), token(id=6, username="lian123")]
        def reads(query):
            if query.startswith("SELECT id,trim(access_token)"):
                return [{"id": 1, "token": "private-admin-test"}]
            if "WHERE t.id=" in query:
                requested = int(query.rsplit("=", 1)[1])
                return [row for row in rows if row["id"] == requested]
            self.assertTrue(query.startswith("SELECT "))
            return rows
        r.rows.side_effect = reads
        module.option_updates.return_value = updates
        pricing = {"model_name": access.MODEL, "enable_groups": ["文"],
                   **{access.PRICE_FIELDS[key]: value for key, value in updates.items()}}
        def get(url, headers=None):
            if url.endswith("/api/option/"):
                return 200, {"success": True, "data": [{"key": key, "value": value} for key, value in runtime.items()]}
            if url.endswith("/api/pricing"):
                return 200, {"success": True, "data": [pricing]}
            self.assertEqual(url, access.PUBLIC + "/v1/models")
            self.assertIn("Authorization", headers)
            return 200, {"data": [{"id": access.MODEL}]}
        verifier.get = Mock(side_effect=get)
        return verifier, r, module, rows, runtime

    def test_end_to_end_audit_uses_runtime_gate_and_gets_all_lian_keys_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            verifier, r, module, _, _ = self.fixture(directory)
            report = verifier.verify()
        self.assertTrue(report["success"], report.get("reason"))
        r.runtime_gate.assert_called_once_with(r.read("new-api-stage.json"))
        self.assertEqual(report["lian_keys_verified"], 2)
        self.assertEqual(len([item for item in report["public_gets"] if item["kind"].endswith("models")]), 3)
        r.sql.assert_not_called()
        module.private_write.assert_called_once()
        self.assertNotIn("private-test-key", json.dumps(report))
        self.assertNotIn("private-admin-test", json.dumps(report))

    def test_runtime_gate_failure_prevents_authenticated_catalog_gets(self):
        with tempfile.TemporaryDirectory() as directory:
            verifier, r, module, _, _ = self.fixture(directory)
            r.runtime_gate.side_effect = RuntimeError("private-internal-value")
            report = verifier.verify()
        self.assertFalse(report["success"])
        verifier.get.assert_not_called()
        r.sql.assert_not_called()
        self.assertNotIn("private-internal-value", json.dumps(report))
        module.private_write.assert_called_once()

    def test_source_priority_swap_fails_before_public_gets(self):
        with tempfile.TemporaryDirectory() as directory:
            verifier, r, _, _, _ = self.fixture(directory)
            routes = r.snapshot_routes.return_value[0]
            routes[0]["priority"], routes[1]["priority"] = 8, 10
            report = verifier.verify()
        self.assertFalse(report["success"])
        verifier.get.assert_not_called()
        r.sql.assert_not_called()

    def test_global_auto_policy_requires_text_even_when_existing_tokens_are_text_only(self):
        with tempfile.TemporaryDirectory() as directory:
            verifier, r, _, rows, runtime = self.fixture(directory)
            for row in rows:
                row["token_group"] = "文"
            runtime["AutoGroups"] = '["图"]'
            report = verifier.verify()
        self.assertFalse(report["success"])
        self.assertEqual(report["public_gets"], [])
        r.sql.assert_not_called()

    def test_policy_failures_keep_public_pricing_and_other_valid_results_without_key_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            verifier, r, _, rows, _ = self.fixture(directory)
            rows[-1].update(model_limits_enabled=True, model_limits="another-model")
            report = verifier.verify()
        self.assertFalse(report["success"])
        self.assertEqual(report["token_audit"]["failures"][0]["reason"], "model_allowlist_blocks_fable")
        self.assertEqual(report["lian_keys_verified"], 1)
        self.assertTrue(any(item["kind"] == "public_pricing" and item["http"] == 200 for item in report["public_gets"]))
        r.sql.assert_not_called()

    def test_http_probe_is_get_only_and_never_follows_credentials_to_redirects(self):
        verifier = access.AccessVerifier()
        response = io.BytesIO(json.dumps({"data": [{"id": access.MODEL}]}).encode())
        response.status = 200
        verifier.opener = Mock()
        verifier.opener.open.return_value = response
        status, _ = verifier.get(access.PUBLIC + "/v1/models", {"Authorization": "Bearer private-test"})
        self.assertEqual(status, 200)
        request = verifier.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(access.NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://other.example"))

    def test_near_expiry_key_is_not_requested_and_no_status_update_is_attempted(self):
        verifier = access.AccessVerifier()
        verifier._rollout = Mock()
        verifier._rollout.rows.return_value = [token(expired_time=1010)]
        verifier.get = Mock()
        with patch.object(access.time, "time", return_value=1000), self.assertRaisesRegex(RuntimeError, "expires too soon"):
            verifier.catalog(token(), POLICY)
        verifier.get.assert_not_called()
        self.assertTrue(verifier._rollout.rows.call_args.args[0].startswith("SELECT "))

    def test_validity_uses_auth_contract_not_balance_or_group_prefilter(self):
        self.assertTrue(access.valid_token(token(), 1000))
        self.assertTrue(access.valid_token(token(expired_time=1000), 1000))
        self.assertFalse(access.valid_token(token(expired_time=999), 1000))
        self.assertFalse(access.valid_token(token(remain_quota=0), 1000))
        self.assertTrue(access.valid_token(token(remain_quota=0, unlimited_quota=True), 1000))
        self.assertFalse(access.valid_token(token(user_status=2), 1000))
        self.assertFalse(access.valid_token(token(expired_time=None), 1000))
        self.assertFalse(access.valid_token(token(remain_quota=None), 1000))

    def test_all_valid_groups_audited_and_missing_exact_whitelist_fails_closed(self):
        rows = [token(), token(id=5, token_group="文"), token(id=6, token_group="default"),
                token(id=7, model_limits_enabled=True, model_limits=" " + access.MODEL)]
        result = access.audit_tokens(rows, POLICY, 1000)
        self.assertEqual(result["valid_tokens"], 4)
        self.assertEqual(result["eligible_tokens"], 2)
        self.assertEqual(result["groups"], {"auto": 2, "文": 1, "default": 1})
        self.assertEqual({row["reason"] for row in result["failures"]}, {"unexpected_token_group", "model_allowlist_blocks_fable"})
        self.assertEqual(access.access_reason(token(model_limits_enabled=True, model_limits=access.MODEL), POLICY), None)

    def test_per_user_special_groups_and_auto_intersection_are_respected(self):
        policy = {**POLICY, "special_groups": {"restricted": {"-:文": "disabled"}}}
        self.assertEqual(access.access_reason(token(user_group="restricted"), policy), "auto_policy_does_not_include_text")
        self.assertEqual(access.access_reason(token(user_group="restricted", token_group="文"), policy), "token_group_not_user_usable")

    def test_no_ip_probe_selection_and_ip_bound_lian_are_not_bypassed(self):
        self.assertTrue(access.ip_allows("", "156.239.3.210"))
        self.assertTrue(access.ip_allows("156.239.3.0/24", "156.239.3.210"))
        self.assertFalse(access.ip_allows("10.0.0.1", "156.239.3.210"))
        with self.assertRaises(RuntimeError):
            access.ip_allows("10.0.0.1,10.0.0.2", "156.239.3.210")
        self.assertTrue(access.ip_allows("invalid\n156.239.3.210", "156.239.3.210"))
        self.assertEqual(access.access_reason(token(allow_ips="invalid"), POLICY), "invalid_ip_allowlist")
        for unsupported in ["156.239.3.210/255.255.255.0", "fe80::1%eth0", "fe80::1%eth0/128"]:
            self.assertEqual(access.access_reason(token(allow_ips=unsupported), POLICY), "invalid_ip_allowlist")
            with self.assertRaises(RuntimeError):
                access.ip_allows(unsupported, "156.239.3.210")
        self.assertTrue(access.ip_allows("::ffff:156.239.3.210", "156.239.3.210"))

    def test_preservation_strips_only_exact_fable_and_retains_other_changes(self):
        before = {"ModelRatio": '{"other":1}', "GroupRatio": '{"文":0.15}'}
        current = {"ModelRatio": '{"other":1,"claude-fable-5-1":2}', "GroupRatio": '{"文":0.15}'}
        self.assertEqual(access.compare_option_delta(before, current), [])
        current["ModelRatio"] = '{"other":99,"claude-fable-5-1":2}'
        self.assertEqual(access.compare_option_delta(before, current), ["ModelRatio"])
        self.assertEqual(access.compare_option_delta({"URL": "https://example.test"}, {"URL": "https://example.test"}), [])
        self.assertEqual(access.compare_option_delta({"URL": "https://example.test"}, {"URL": "https://example.test", "unexpected": "1"}), ["unexpected"])

    def test_public_pricing_requires_complete_six_dimensions_and_text_enablement(self):
        updates = {"ModelRatio": 2, "CompletionRatio": 5, "CacheRatio": .1, "CreateCacheRatio": 1.25,
                   "billing_setting.billing_mode": "tiered_expr", "billing_setting.billing_expr": 'tier("base", p*4+c*20)'}
        row = {"model_name": access.MODEL, "enable_groups": ["文"], "model_ratio": 2, "completion_ratio": 5,
               "cache_ratio": .1, "create_cache_ratio": 1.25, "billing_mode": "tiered_expr", "billing_expr": updates["billing_setting.billing_expr"]}
        access.verify_pricing_row(row, updates)
        with self.assertRaises(RuntimeError):
            access.verify_pricing_row({**row, "cache_ratio": .2}, updates)
        with self.assertRaises(RuntimeError):
            access.verify_pricing_row({**row, "enable_groups": ["图"]}, updates)

    def test_old_metadata_drift_fails_while_new_exact_fable_is_ignored(self):
        before = [{"id": 2, "model_name": "old-model", "description": "preserved", "status": 1}]
        unchanged = [*before, {"id": 99, "model_name": access.MODEL, "description": "new Fable", "status": 1}]
        self.assertEqual(access.old_metadata_delta(before, unchanged), [])
        changed = [{**before[0], "description": "changed"}, unchanged[1]]
        self.assertEqual(access.old_metadata_delta(before, changed), [2])
        self.assertEqual(access.old_metadata_delta(before, []), [2])


if __name__ == "__main__":
    unittest.main()
