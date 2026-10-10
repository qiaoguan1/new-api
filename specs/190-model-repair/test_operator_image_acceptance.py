"""No-charge RED/GREEN tests for the bounded root acceptance workflow."""
import base64
import copy
import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location("operator_acceptance190", pathlib.Path(__file__).with_name("operator_image_acceptance.py"))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


def png():
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1024, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * 3072) * 1024)) + chunk(b"IEND", b"")


class FakeBackend:
    def __init__(self):
        self.user = {"id": 1, "username": "qiaoguan", "role": 100, "status": 1, "quota": 10000000}
        self.facts = {"key_match_count": 1, "observed_at": 1000, "native_routes_verified": True, "production_token": {"id": 44, "status": 1, "group": "默认通道", "model_limits_enabled": False, "expired_time": -1, "unlimited_quota": True, "remain_quota": 0}, "nody_self": {"id": 2, "quota": 5000000},
            "upstream_pricing": {"group_ratio": {"默认通道": 1}, "data": [{"model_name": model, "quota_type": 1, "model_price": 0.2} for model in app.MODELS]},
            "native_pricing": {"group_ratio": {"图": 0.15}, "data": [{"model_name": model, "quota_type": 1, "model_price": 3} for model in app.MODELS]}}
        self.posts = []
        self.created = []
        self.disabled = []
        self.tokens = {}
        self.no_receipt = False
        self.error = None
        self.invalid_image = False
        self.native_has_provider_id = True
        self.response_provider_id = False

    def operator(self): return copy.deepcopy(self.user)
    def quote_facts(self): return copy.deepcopy(self.facts)
    def create_token(self, name, key, expiry):
        self.created.append((name, key, expiry))
        row = {"id": 900, "user_id": 1, "key": key, "name": name, "status": 1, "expired_time": expiry, "remain_quota": 600000, "used_quota": 0, "unlimited_quota": False, "model_limits_enabled": True, "model_limits": ",".join(app.MODELS), "group": "图", "cross_group_retry": False}
        self.tokens[900] = row
        return copy.deepcopy(row)
    def token(self, token_id): return copy.deepcopy(self.tokens[token_id])
    def post(self, body, headers):
        self.posts.append((copy.deepcopy(body), dict(headers)))
        if self.error: raise self.error
        self.tokens[900]["used_quota"] += 225000
        self.tokens[900]["remain_quota"] -= 225000
        image = b"invalid" if self.invalid_image else png()
        headers = {"X-XingTu-Relay-Request-ID": "native-" + body["model"]}
        if self.response_provider_id: headers["X-Oneapi-Request-Id"] = "nody-" + body["model"]
        return 200, headers, json.dumps({"data": [{"b64_json": base64.b64encode(image).decode()}]}).encode()
    def native_logs(self, token_id):
        return [{"type": 2, "user_id": 1, "token_id": token_id, "model_name": body["model"], "request_id": "native-" + body["model"], "upstream_request_id": "nody-" + body["model"] if self.native_has_provider_id else "", "quota": 225000} for body, _ in self.posts] if not self.error else []
    def upstream_logs(self):
        if self.no_receipt: return []
        return [{"type": 2, "model_name": body["model"], "request_id": "nody-" + body["model"], "quota": 100000, "token_id": 44, "group": "默认通道"} for body, _ in self.posts]
    def disable_token(self, record): self.disabled.append(record["id"]); self.tokens[record["id"]]["status"] = 2
    def image_url_bytes(self, url): raise AssertionError("No URL request in these local fixtures")


class AcceptanceTests(unittest.TestCase):
    def create(self, directory):
        backend = FakeBackend()
        clock = [1000.0]
        operation = app.OperatorAcceptance(pathlib.Path(directory) / "private", backend, "a" * 32, lambda: clock[0])
        return operation, backend, clock

    def test_two_exact_skus_reconcile_authenticated_cost_and_disable_only_operator_token(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            self.assertEqual(len(backend.created), 1)
            for model in app.MODELS:
                self.assertEqual(operation.submit(model)["state"], "awaiting_reconcile")
                result = operation.reconcile(model)
                self.assertEqual(result["state"], "reconciled")
                self.assertEqual(result["actual_cost_cny_exact"], "0.300000")
                self.assertEqual(result["native_quota"], 225000)
            operation.close()
            self.assertEqual(backend.disabled, [900])
            self.assertEqual(len(backend.posts), 2)
            for body, headers in backend.posts:
                self.assertEqual((body["size"], body["n"], body["quality"]), ("1024x1024", 1, "auto"))
                self.assertEqual(headers["Idempotency-Key"], headers["X-Request-ID"])
                self.assertTrue(headers["Authorization"].startswith("Bearer sk-"))

    def test_wrong_operator_or_missing_cost_unit_never_creates_test_token(self):
        for invalid in ("operator", "price"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                operation, backend, _ = self.create(directory)
                if invalid == "operator": backend.user["id"] = 2
                else: backend.facts["upstream_pricing"]["data"][0]["quota_type"] = 0
                with self.assertRaises(app.SafetyError): operation.prepare()
                self.assertEqual(backend.created, [])
                self.assertEqual(backend.posts, [])

    def test_changed_fresh_quote_aborts_before_post(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.facts["upstream_pricing"]["data"][0]["model_price"] = 0.3
            with self.assertRaises(app.SafetyError): operation.submit(app.MODELS[0])
            self.assertEqual(backend.posts, [])

    def test_unknown_post_is_persisted_and_never_replayed_or_followed_by_another_model(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.error = TimeoutError("fixture unknown")
            self.assertEqual(operation.submit(app.MODELS[0])["state"], "uncertain")
            for model in app.MODELS:
                with self.assertRaises(app.SafetyError): operation.submit(model)
            with self.assertRaises(app.SafetyError): operation.close()
            self.assertEqual(len(backend.posts), 1)
            self.assertEqual(backend.disabled, [])
            self.assertTrue((operation.root / (app.MODELS[0] + ".intent.json")).is_file())

    def test_no_exact_receipt_keeps_actual_cost_unknown_and_blocks_next_post(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            operation.submit(app.MODELS[0])
            backend.no_receipt = True
            result = operation.reconcile(app.MODELS[0])
            self.assertIsNone(result["actual_cost_cny_exact"])
            self.assertEqual(result["state"], "pending_cost")
            with self.assertRaises(app.SafetyError): operation.submit(app.MODELS[1])
            self.assertEqual(len(backend.posts), 1)

    def test_invalid_image_does_not_claim_generation_delivery_or_allow_next_post(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.invalid_image = True
            self.assertEqual(operation.submit(app.MODELS[0])["state"], "uncertain")
            with self.assertRaises(app.SafetyError): operation.submit(app.MODELS[1])
            self.assertEqual(len(backend.posts), 1)

    def test_original_pending_intent_survives_restart_and_cannot_resubmit(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, clock = self.create(directory)
            operation.prepare()
            backend.error = TimeoutError("fixture")
            operation.submit(app.MODELS[0])
            restarted = app.OperatorAcceptance(operation.root, backend, "a" * 32, lambda: clock[0])
            with self.assertRaises(app.SafetyError): restarted.submit(app.MODELS[0])
            self.assertEqual(len(backend.posts), 1)

    def test_two_process_instances_cannot_race_unresolved_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, clock = self.create(directory)
            operation.prepare()
            other = app.OperatorAcceptance(operation.root, backend, "a" * 32, lambda: clock[0])
            with operation.stage_lock():
                with self.assertRaises(app.SafetyError): other.submit(app.MODELS[1])
            self.assertEqual(backend.posts, [])

    def test_route_proof_required_before_token_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            backend.facts["native_routes_verified"] = False
            with self.assertRaises(app.SafetyError): operation.prepare()
            self.assertEqual(backend.created, [])

    def test_production_key_limit_or_expiry_failure_does_not_create_new_operator_key(self):
        for change in ({"unlimited_quota": False, "remain_quota": 1}, {"expired_time": 999}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                operation, backend, _ = self.create(directory)
                backend.facts["production_token"].update(change)
                with self.assertRaises(app.SafetyError): operation.prepare()
                self.assertEqual(backend.created, [])

    def test_headers_and_balance_changes_cannot_replace_exact_upstream_request_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            operation.submit(app.MODELS[0])
            original = backend.upstream_logs
            backend.upstream_logs = lambda: [{**row, "request_id": "unrelated-request"} for row in original()]
            backend.facts["nody_self"]["quota"] -= 100000
            result = operation.reconcile(app.MODELS[0])
            self.assertEqual((result["state"], result["actual_cost_cny_exact"]), ("pending_cost", None))
            self.assertEqual(len(backend.posts), 1)

    def test_authenticated_unexpected_bill_is_preserved_and_blocks_next_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            operation.submit(app.MODELS[0])
            original = backend.upstream_logs
            backend.upstream_logs = lambda: [{**row, "quota": 200000} for row in original()]
            result = operation.reconcile(app.MODELS[0])
            self.assertEqual((result["state"], result["actual_cost_cny_exact"]), ("billing_bound_exceeded", "0.600000"))
            with self.assertRaises(app.SafetyError): operation.submit(app.MODELS[1])

    def test_expired_unknown_key_is_disabled_only_after_readonly_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, clock = self.create(directory)
            operation.prepare()
            backend.error = TimeoutError("fixture")
            operation.submit(app.MODELS[0])
            clock[0] = 4700
            result = operation.close()
            self.assertEqual(result["unresolved"], [app.MODELS[0]])
            self.assertTrue((operation.root / (app.MODELS[0] + ".reconcile.json")).is_file())
            self.assertEqual(backend.disabled, [900])

    def test_original_response_exact_chain_recovers_legacy_missing_native_upstream_id(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.native_has_provider_id = False
            backend.response_provider_id = True
            operation.submit(app.MODELS[0])
            result = operation.reconcile(app.MODELS[0])
            self.assertEqual((result["state"], result["actual_cost_cny_exact"]), ("reconciled", "0.300000"))
            self.assertEqual(result["request_id_evidence"], "owned_response_exact_chain")
            self.assertEqual(len(result["response_sha256"]), 64)
            self.assertEqual(len(backend.posts), 1)

    def test_wrong_relay_or_ordinary_native_id_cannot_be_used_as_provider_id(self):
        for invalid in ("relay", "provider"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                operation, backend, _ = self.create(directory)
                operation.prepare()
                backend.native_has_provider_id = False
                backend.response_provider_id = True
                operation.submit(app.MODELS[0])
                path = operation.root / (app.MODELS[0] + ".response.json")
                response = json.loads(path.read_text())
                field = "X-XingTu-Relay-Request-ID" if invalid == "relay" else "X-Oneapi-Request-Id"
                response["headers"][field] = "unrelated-relay" if invalid == "relay" else "native-" + app.MODELS[0]
                path.write_text(json.dumps(response))
                result = operation.reconcile(app.MODELS[0])
                self.assertEqual((result["state"], result["actual_cost_cny_exact"]), ("pending_cost", None))
                self.assertTrue(result.get("reason"))
                with self.assertRaises(app.SafetyError): operation.submit(app.MODELS[1])

    def test_claimed_native_id_and_original_provider_header_conflict_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.response_provider_id = True
            operation.submit(app.MODELS[0])
            native_logs = backend.native_logs
            backend.native_logs = lambda token: [{**row, "upstream_request_id": "conflicting-original-provider"} for row in native_logs(token)]
            with self.assertRaises(app.SafetyError): operation.reconcile(app.MODELS[0])
            self.assertEqual(len(backend.posts), 1)

    def test_response_hash_pin_refuses_later_mutation_without_rewriting_known_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.native_has_provider_id = False
            backend.response_provider_id = True
            operation.submit(app.MODELS[0])
            operation.reconcile(app.MODELS[0])
            path = operation.root / (app.MODELS[0] + ".response.json")
            response = json.loads(path.read_text())
            response["headers"]["X-Oneapi-Request-Id"] = "different-provider"
            path.write_text(json.dumps(response))
            with self.assertRaises(app.SafetyError): operation.reconcile(app.MODELS[0])
            saved = json.loads((operation.root / (app.MODELS[0] + ".reconcile.json")).read_text())
            self.assertEqual(saved["actual_cost_cny_exact"], "0.300000")
            self.assertEqual(len(backend.posts), 1)

    def test_alternative_chain_does_not_bypass_current_expected_provider_route(self):
        with tempfile.TemporaryDirectory() as directory:
            operation, backend, _ = self.create(directory)
            operation.prepare()
            backend.native_has_provider_id = False
            backend.response_provider_id = True
            operation.submit(app.MODELS[0])
            backend.facts["native_routes_verified"] = False
            result = operation.reconcile(app.MODELS[0])
            self.assertEqual((result["state"], result["actual_cost_cny_exact"]), ("pending_cost", None))
            self.assertIn("route", result["reason"])


if __name__ == "__main__": unittest.main()
