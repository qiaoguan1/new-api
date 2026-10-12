"""No-charge contracts for the captured legacy Image2.5 adapter."""

import http.client
import importlib.util
import io
import json
import pathlib
import sys
import threading
import unittest
import urllib.error
from unittest import mock

io_spec = importlib.util.spec_from_file_location("image25_test_io", pathlib.Path(__file__).with_name("image_io.py"))
image_io = importlib.util.module_from_spec(io_spec)
io_spec.loader.exec_module(image_io)
spec = importlib.util.spec_from_file_location("image25_callability191", pathlib.Path(__file__).with_name("app.py"))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
with mock.patch.dict(sys.modules, {"image_io": image_io}):
    spec.loader.exec_module(app)


class Image25CallabilityTests(unittest.TestCase):
    def body(self, **changes):
        return {"model": "gpt-image-2.5", "prompt": "fixture", **changes}

    def request(self, provider, path="/v1/images/generations", body=None, method="POST", *, headers=None, include_headers=False):
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port)
            client.request(method, "/" + provider + path, body=json.dumps(body) if body is not None else None, headers={"Authorization": "Bearer fixture", "Content-Type": "application/json", **(headers or {})})
            response = client.getresponse()
            value = response.status, response.getheader("X-XingTu-Image-Submission-State"), json.loads(response.read())
            if include_headers:
                value = (*value, dict(response.getheaders()))
            client.close()
            return value
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def config(self):
        return mock.patch.object(app, "CONFIG", {"adapter_token": "fixture", "providers": {provider: {"base_url": "https://example.invalid", "key": "fixture-only"} for provider in ("rolldek", "haina")}})

    def test_provider_resolution_capabilities_do_not_claim_rolldek_4k(self):
        with self.config():
            for provider, expected in [("rolldek", ["1024x1024"]), ("haina", sorted(app.SIZES))]:
                status, _, data = self.request(provider, path="/v1/models", method="GET")
                self.assertEqual(status, 200)
                caps = data["data"][0]["image_capabilities"]
                self.assertEqual(caps["sizes"], expected)
                self.assertEqual(caps["endpoints"], ["/v1/images/generations"])
                self.assertFalse(caps["image_edit"])

    def test_edit_and_rolldek_2k_are_definite_no_submit_not_downgraded(self):
        for provider, path, body in [("haina", "/v1/images/edits", self.body()), ("rolldek", "/v1/images/generations", self.body(size="2048x2048"))]:
            with self.subTest(provider=provider), self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
                status, state, data = self.request(provider, path, body)
                self.assertIn(status, [400, 429])
                self.assertEqual(state, "not_submitted")
                self.assertIn("requires_other_route", data["error"]["message"])
                upstream.assert_not_called()

    def test_timeout_after_post_is_not_a_client_input_error(self):
        with self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
            upstream.return_value.open.side_effect = TimeoutError("fixture timeout")
            status, state, data = self.request("haina", body=self.body())
            self.assertEqual((status, state), (502, "uncertain"))
            self.assertEqual(data["error"]["code"], "upstream_outcome_unconfirmed")
            self.assertEqual(upstream.return_value.open.call_count, 1)

    def test_invalid_upstream_results_remain_uncertain(self):
        for result in [{"data": []}, {"data": [{"url": "https://["}]}, {"data": [{"b64_json": "invalid"}]}]:
            with self.subTest(result=result), self.assertRaises(app.Rejection) as raised:
                app.response_payload(result, "b64_json")
            self.assertEqual((raised.exception.status, raised.exception.submission_state), (502, "uncertain"))

    def test_original_model_size_and_quality_boundaries_unchanged(self):
        for size in sorted(app.SIZES):
            self.assertEqual(app.validate(self.body(size=size), "haina")["size"], size)
        for changes in [{"model": "gpt-image-2.5-sunburst"}, {"n": 2}, {"size": "auto"}, {"quality": "high"}, {"image": "https://example.invalid/image.png"}]:
            with self.subTest(changes=changes), self.assertRaises(app.Rejection):
                app.validate(self.body(**changes), "haina")

    def test_local_busy_is_definite_and_does_not_contact_provider(self):
        with self.config(), mock.patch.object(app, "ACTIVE") as active, mock.patch.object(app.urllib.request, "build_opener") as upstream:
            active.acquire.return_value = False
            status, state, data = self.request("haina", body=self.body())
            self.assertEqual((status, state), (429, "not_submitted"))
            self.assertIn("upstream_rejected_no_task", data["error"]["message"])
            upstream.assert_not_called()
            active.release.assert_not_called()

    def test_request_id_validation_is_bounded_and_rejects_credentials_or_control_chars(self):
        for value in [None, 123, "", "with space", "a" * 129, "sk-accidental-key", "request\r\nAuthorization: secret", "request\tvalue", "/path"]:
            with self.subTest(value=value):
                self.assertEqual(app.valid_request_id(value), "")
        for value in ["ops191.a-b_1:2", "a", "a" * 128]:
            self.assertEqual(app.valid_request_id(value), value)

    def test_safe_input_request_id_priority_without_forwarding_unrelated_headers(self):
        cases = [
            ({"X-Oneapi-Request-Id": "native-first", "X-XingTu-Relay-Request-ID": "relay-second", "X-Request-ID": "caller-third"}, "native-first"),
            ({"X-Oneapi-Request-Id": "sk-accidental-key", "X-XingTu-Relay-Request-ID": "relay-second", "X-Request-ID": "caller-third"}, "relay-second"),
            ({"X-Oneapi-Request-Id": "invalid space", "X-XingTu-Relay-Request-ID": "invalid space", "X-Request-ID": "caller-third"}, "caller-third"),
            ({"X-Oneapi-Request-Id": "sk-accidental-key", "X-Request-ID": "invalid space"}, None),
        ]
        for headers, expected in cases:
            with self.subTest(expected=expected), self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
                response = upstream.return_value.open.return_value.__enter__.return_value
                response.headers = {}
                response.read.return_value = b'{"data":[{"url":"https://example.invalid/image.png"}]}'
                status, state, _, returned = self.request("haina", body=self.body(), headers={**headers, "Cookie": "do-not-forward"}, include_headers=True)
                self.assertEqual((status, state), (200, "submitted"))
                request = upstream.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header("X-request-id"), expected)
                self.assertIsNone(request.get_header("Cookie"))
                self.assertEqual(request.get_header("Authorization"), "Bearer fixture-only")
                self.assertNotIn("X-Oneapi-Request-Id", returned)
                self.assertEqual(upstream.return_value.open.call_count, 1)

    def test_returned_upstream_id_comes_from_provider_not_input(self):
        cases = [
            ({"X-Oneapi-Request-Id": "provider-first", "X-Request-ID": "provider-second"}, "provider-first"),
            ({"X-Oneapi-Request-Id": "sk-accidental-key", "X-Request-ID": "provider-second"}, "provider-second"),
            ({"X-Oneapi-Request-Id": "invalid\r\nheader", "X-Request-ID": "sk-secret"}, None),
            ({}, None),
        ]
        for headers, expected in cases:
            with self.subTest(expected=expected), self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
                response = upstream.return_value.open.return_value.__enter__.return_value
                response.headers = {**headers, "Authorization": "Bearer never-forward"}
                response.read.return_value = b'{"data":[{"url":"https://example.invalid/image.png"}]}'
                status, state, _, returned = self.request("haina", body=self.body(), headers={"X-Request-ID": "input-not-a-receipt"}, include_headers=True)
                self.assertEqual((status, state), (200, "submitted"))
                self.assertEqual(returned.get("X-Oneapi-Request-Id"), expected)
                self.assertNotIn("Authorization", returned)

    def test_actual_upstream_id_survives_http_error_without_replay(self):
        error_body = io.BytesIO(b'unknown')
        error = urllib.error.HTTPError("https://example.invalid", 503, "unavailable", {"X-Oneapi-Request-Id": "provider-original"}, error_body)
        with self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
            upstream.return_value.open.side_effect = error
            status, state, data, returned = self.request("haina", body=self.body(), headers={"X-Request-ID": "input-id"}, include_headers=True)
            self.assertEqual((status, state), (502, "uncertain"))
            self.assertEqual(data["error"]["code"], "upstream_outcome_unconfirmed")
            self.assertEqual(returned.get("X-Oneapi-Request-Id"), "provider-original")
            self.assertTrue(error_body.closed)
            self.assertEqual(upstream.return_value.open.call_count, 1)

    def test_known_provider_id_survives_body_or_transfer_failure(self):
        for content in [b'not JSON', b'{"data":[]}', b'{"data":[{"url":"https://example.invalid/image.png"}]}']:
            with self.subTest(content=content), self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream, mock.patch.object(app, "image_bytes", side_effect=TimeoutError("transfer")):
                response = upstream.return_value.open.return_value.__enter__.return_value
                response.headers = {"X-Request-ID": "provider-body-id"}
                response.read.return_value = content
                status, state, _, returned = self.request("haina", body=self.body(response_format="b64_json"), include_headers=True)
                self.assertEqual((status, state), (502, "uncertain"))
                self.assertEqual(returned.get("X-Oneapi-Request-Id"), "provider-body-id")
                self.assertEqual(upstream.return_value.open.call_count, 1)

    def test_timeout_does_not_invent_provider_id_from_input(self):
        with self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
            upstream.return_value.open.side_effect = TimeoutError("fixture timeout")
            status, state, _, returned = self.request("haina", body=self.body(), headers={"X-Request-ID": "input-not-receipt"}, include_headers=True)
            self.assertEqual((status, state), (502, "uncertain"))
            self.assertNotIn("X-Oneapi-Request-Id", returned)
            self.assertEqual(upstream.return_value.open.call_count, 1)

    def test_provider_id_is_saved_before_body_read_timeout(self):
        with self.config(), mock.patch.object(app.urllib.request, "build_opener") as upstream:
            response = upstream.return_value.open.return_value.__enter__.return_value
            response.headers = {"X-Oneapi-Request-Id": "provider-before-timeout"}
            response.read.side_effect = TimeoutError("fixture body timeout")
            status, state, _, returned = self.request("haina", body=self.body(), include_headers=True)
            self.assertEqual((status, state), (502, "uncertain"))
            self.assertEqual(returned.get("X-Oneapi-Request-Id"), "provider-before-timeout")
            self.assertEqual(upstream.return_value.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
