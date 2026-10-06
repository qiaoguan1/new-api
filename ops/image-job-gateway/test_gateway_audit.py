import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

PATH = pathlib.Path(__file__).with_name("app.py")
spec = importlib.util.spec_from_file_location("image_gateway_audit", PATH)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class ImageAuditTests(unittest.TestCase):
    def gateway(self, directory):
        return app.Gateway(app.Config(token="local-test", upstream_api_key="test-only", upstream_base_url="http://127.0.0.1:1/v1", data_dir=pathlib.Path(directory), worker_concurrency=1))

    def create(self, gateway, mode="generations"):
        payload = {"model": "gpt-image-2", "prompt": "test", "size": "1024x1024", "n": 1, "reference_images": [{"url": "https://example.invalid/ref.png"}] if mode == "edits" else []}
        raw = json.dumps(payload)
        job, _ = gateway.store.create("btask_test", hashlib.sha256(raw.encode()).hexdigest(), mode, raw)
        return job["job_id"], raw

    def test_reference_fetch_failure_is_definite_failure_before_any_post(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, _ = self.create(gateway, "edits")
            calls = []
            def fail(request, **_):
                calls.append(request.get_method())
                raise urllib.error.URLError("reference unavailable")
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=fail):
                gateway._run(job)
            result = gateway.store.get(job_id=job)
            self.assertEqual((result["status"], result["phase"], result["error"]["code"]), ("failed", "reference_fetch", "reference_fetch_failed"))
            self.assertEqual(calls, ["GET"])

    def test_ambiguous_post_is_not_replayed_for_same_job(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, raw = self.create(gateway)
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=urllib.error.URLError("connection reset")) as opened:
                gateway._run(job)
                gateway._run(job)
            result = gateway.store.get(job_id=job)
            self.assertEqual((result["status"], result["phase"]), ("uncertain", "upstream_submit"))
            self.assertEqual(opened.call_count, 1)
            existing, reused = gateway.store.create("btask_test", hashlib.sha256(raw.encode()).hexdigest(), "generations", raw)
            self.assertTrue(reused)
            self.assertEqual(existing["job_id"], job)

    def test_relay_header_survives_body_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, _ = self.create(gateway)
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.headers = {"X-XingTu-Relay-Request-ID": "relay-exact-id", "X-Oneapi-Request-Id": "provider-id"}
            response.read.side_effect = TimeoutError("body timed out")
            with mock.patch.object(app.urllib.request, "urlopen", return_value=response):
                gateway._run(job)
            result = gateway.store.get(job_id=job)
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(result["relay_request_id"], "relay-exact-id")
            self.assertEqual(result["upstream_request_id"], "provider-id")

    def test_native_uncertainty_http_error_preserves_exact_id_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, _ = self.create(gateway)
            error = urllib.error.HTTPError("http://127.0.0.1", 504, "timeout", {"X-XingTu-Relay-Request-ID": "relay-native-id"}, io.BytesIO(b'{"error":{"code":"image_submit_uncertain"}}'))
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=error) as opened:
                gateway._run(job)
                gateway._run(job)
            result = gateway.store.get(job_id=job)
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(result["relay_request_id"], "relay-native-id")
            self.assertEqual(opened.call_count, 1)

    def test_cleanup_keeps_uncertain_minimal_audit_beyond_metadata_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, _ = self.create(gateway)
            gateway.store.claim(job)
            gateway.store.phase(job, "upstream_submit", relay_request_id="exact-native-id")
            gateway.store.finish(job, "uncertain", code="upstream_submit_uncertain")
            connection = gateway.store.connect()
            connection.execute("update image_jobs set finished_at=1 where job_id=?", (job,))
            connection.close()
            gateway.store.cleanup_expired()
            result = gateway.store.get(job_id=job)
            self.assertEqual(result["relay_request_id"], "exact-native-id")
            connection = gateway.store.connect()
            row = connection.execute("select payload_json,audit_json from image_jobs where job_id=?", (job,)).fetchone()
            connection.close()
            self.assertEqual(row["payload_json"], "")
            self.assertEqual(json.loads(row["audit_json"])["size"], "1024x1024")
            self.assertNotIn("prompt", row["audit_json"])

    def test_restart_before_submit_does_not_create_uncertain_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, _ = self.create(gateway, "edits")
            gateway.store.claim(job)
            self.assertEqual(gateway.store.recover(), [])
            result = gateway.store.get(job_id=job)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "gateway_restart_before_submit")

    def test_error_body_timeout_does_not_leave_active_job(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            job, _ = self.create(gateway)
            fp = mock.Mock()
            fp.read.side_effect = TimeoutError("slow error body")
            error = urllib.error.HTTPError("http://127.0.0.1", 502, "proxy error", {"X-XingTu-Relay-Request-ID": "exact-id"}, fp)
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=error):
                gateway._run(job)
            result = gateway.store.get(job_id=job)
            self.assertEqual((result["status"], gateway.store.active_count()), ("uncertain", 0))
            self.assertEqual(result["relay_request_id"], "exact-id")
            fp.close.assert_called_once()

    def test_plain_proxy_timeout_and_empty_success_are_not_confirmed_failures(self):
        for status in [504, 200]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                gateway = self.gateway(directory)
                job, _ = self.create(gateway)
                if status == 504:
                    error = urllib.error.HTTPError("http://127.0.0.1", status, "proxy timeout", {}, io.BytesIO(b'<html>timeout</html>'))
                    opened = mock.patch.object(app.urllib.request, "urlopen", side_effect=error)
                else:
                    response = mock.MagicMock()
                    response.__enter__.return_value = response
                    response.headers = {"X-XingTu-Relay-Request-ID": "exact-id"}
                    response.read.return_value = b'{"data":[]}'
                    opened = mock.patch.object(app.urllib.request, "urlopen", return_value=response)
                with opened:
                    gateway._run(job)
                self.assertEqual(gateway.store.get(job_id=job)["status"], "uncertain")
                self.assertEqual(gateway.store.active_count(), 0)

    def test_native_no_submit_rejections_cannot_trip_generation_circuit(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self.gateway(directory)
            for index in range(2):
                raw = json.dumps({"model": "banana-pro", "size": "2048x2048", "prompt": "test"})
                snapshot, _ = gateway.store.create("btask_"+str(index), hashlib.sha256(raw.encode()).hexdigest(), "generations", raw)
                error = urllib.error.HTTPError("http://127.0.0.1", 503, "no compatible route", {"X-XingTu-Relay-Request-ID": "local-exact-id", "X-XingTu-Image-Submission-State": "not_submitted"}, io.BytesIO(b'{"error":{"code":"get_channel_failed"}}'))
                with mock.patch.object(app.urllib.request, "urlopen", side_effect=error):
                    gateway._run(snapshot["job_id"])
                self.assertEqual(gateway.store.get(job_id=snapshot["job_id"])["status"], "failed")
            self.assertFalse(gateway.circuit_snapshot()["open"])


if __name__ == "__main__":
    unittest.main()
