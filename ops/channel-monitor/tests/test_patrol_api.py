import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "patrol_api.py"
SPEC = importlib.util.spec_from_file_location("patrol_api", MODULE_PATH)
api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = api
SPEC.loader.exec_module(api)


class PatrolApiSecurityTests(unittest.TestCase):
    def test_supervised_server_can_rebind_after_clean_restart(self):
        self.assertTrue(api.PatrolHttpServer.allow_reuse_address)

    def test_bind_is_restricted_to_ipv4_loopback_and_bounded_port(self):
        self.assertEqual(api.validate_bind("127.0.0.1", 8793), ("127.0.0.1", 8793))
        for host in ("0.0.0.0", "::", "localhost", "10.0.0.1"):
            with self.assertRaisesRegex(api.ApiError, "bind_not_loopback"):
                api.validate_bind(host, 8793)
        with self.assertRaisesRegex(api.ApiError, "port_invalid"):
            api.validate_bind("127.0.0.1", 0)

    def test_bearer_comparison_rejects_missing_malformed_and_wrong_tokens(self):
        self.assertTrue(api.authorized("Bearer correct", "correct"))
        self.assertFalse(api.authorized("bearer correct", "correct"))
        self.assertFalse(api.authorized("Bearer wrong", "correct"))
        self.assertFalse(api.authorized("", "correct"))

    def test_token_must_be_regular_bounded_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            token = pathlib.Path(directory) / "token"
            token.write_text("a" * 64, encoding="ascii")
            os.chmod(token, 0o600)
            self.assertEqual(api.read_token(token), "a" * 64)
            link = pathlib.Path(directory) / "link"
            try:
                link.symlink_to(token)
            except OSError:
                return
            with self.assertRaisesRegex(api.ApiError, "token_file_unsafe"):
                api.read_token(link)

    def test_trigger_is_atomic_bounded_and_contains_no_request_data(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "run.request"
            api.write_trigger(target, now=123)
            self.assertEqual(json.loads(target.read_text()), {"requested_at": 123})
            self.assertEqual(target.stat().st_size < 128, True)
            if os.name == "posix":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_status_reader_returns_only_sanitized_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            path.write_text(json.dumps({
                "generated_at": 123,
                "generated_at_iso": "2026-08-13T09:15:00+08:00",
                "summary": {"healthy": 17, "failed": 1},
                "incidents": [{"check_id": "docker.new_api", "status": "failed", "severity": "critical", "code": "not_running", "token": "secret"}],
                "private": "secret",
            }), encoding="utf-8")
            status = api.read_status(path)
            serialized = json.dumps(status)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("private", serialized)
            self.assertEqual(status["incidents"][0]["code"], "not_running")

    def test_http_status_and_trigger_require_bearer_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            status = root / "status.json"
            status.write_text(json.dumps({"generated_at": 123, "summary": {"healthy": 1}, "incidents": []}))
            trigger = root / "run.request"
            server = api.PatrolHttpServer(("127.0.0.1", 0), api.handler_factory("correct-token-value-which-is-long", status, trigger))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + "/v1/status", timeout=2)
                self.assertEqual(denied.exception.code, 401)
                request = urllib.request.Request(base + "/v1/status", headers={"Authorization": "Bearer correct-token-value-which-is-long"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                run = urllib.request.Request(base + "/v1/run", method="POST", headers={"Authorization": "Bearer correct-token-value-which-is-long"})
                with urllib.request.urlopen(run, timeout=2) as response:
                    self.assertEqual(response.status, 202)
                self.assertTrue(trigger.is_file())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_local_client_allows_only_fixed_operations(self):
        with self.assertRaisesRegex(api.ApiError, "operation_not_allowed"):
            api.call_local_api("long-enough-token-value-for-test", "http://attacker.example")


if __name__ == "__main__":
    unittest.main()
