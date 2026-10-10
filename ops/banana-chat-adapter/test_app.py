"""No-charge regression tests for the production Banana request adapter."""

import importlib.util
import http.client
import io
import json
import pathlib
import sys
import threading
import unittest
import urllib.error
from unittest import mock

spec = importlib.util.spec_from_file_location("banana_callability191", pathlib.Path(__file__).with_name("app.py"))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class BananaSubmissionSafetyTests(unittest.TestCase):
    def request(self):
        return app.GenerationRequest("banana-flash", "fixture", "1024x1024")

    def route(self, provider="fixture"):
        return app.Route(provider, "https://example.invalid/v1", "not-a-live-key", "gemini-3.1-flash-image-preview")

    def test_only_definite_fast_4xx_rejections_allow_fallback(self):
        for status, message in [(402, "insufficient quota"), (429, "insufficient balance"), (429, "no available image quota"), (404, "model_not_found"), (429, "no available channel")]:
            with self.subTest(status=status, message=message):
                self.assertTrue(app.safe_explicit_rejection(status, message, 1.0))
        for status, message in [(503, "temporarily unavailable"), (502, "currently overloaded"), (500, "no available channel"), (402, "payment pending"), (429, "currently overloaded"), (403, "permission denied"), (408, "no available channel")]:
            with self.subTest(status=status, message=message):
                self.assertFalse(app.safe_explicit_rejection(status, message, 1.0))
        self.assertFalse(app.safe_explicit_rejection(429, "insufficient quota", 6.0))

    def test_exhausted_definite_routes_return_no_task_evidence(self):
        rejected = app.UpstreamRejected("fixture", 402, "insufficient quota", 1.0)
        self.assertEqual(rejected.status, 429)
        self.assertEqual(rejected.code, "upstream_rejected_no_task")
        self.assertIn("upstream_rejected_no_task", rejected.message)
        self.assertIn("insufficient quota", rejected.message)
        self.assertNotIn("raw-secret", app.error_response(app.UpstreamRejected("fixture", 404, "model_not_found raw-secret", 1.0))["error"]["message"])

    def test_uncertain_route_is_not_replayed_on_next_provider(self):
        caller = mock.Mock(side_effect=app.UpstreamUncertain("first", "timeout"))
        routes = {"banana-flash": (self.route("first"), self.route("second"))}
        with self.assertRaises(app.UpstreamUncertain):
            app.generate_with_routes(self.request(), routes, caller)
        self.assertEqual(caller.call_count, 1)

    def test_definite_rejection_can_use_same_model_next_provider(self):
        expected = app.UpstreamResult("second", ["https://example.invalid/image.png"], 200, 1.0)
        caller = mock.Mock(side_effect=[app.UpstreamRejected("first", 402, "insufficient quota", 1.0), expected])
        result = app.generate_with_routes(self.request(), {"banana-flash": (self.route("first"), self.route("second"))}, caller)
        self.assertIs(result, expected)
        self.assertEqual([call.args[1].model for call in caller.call_args_list], ["banana-flash", "banana-flash"])

    def test_error_body_timeout_is_uncertain_and_closes_response(self):
        body = mock.Mock()
        body.read.side_effect = TimeoutError("fixture body timeout")
        error = urllib.error.HTTPError("https://example.invalid", 429, "rejection", {}, body)
        with mock.patch.object(app.urllib.request, "build_opener") as opened:
            opened.return_value.open.side_effect = error
            with self.assertRaises(app.UpstreamUncertain):
                app.call_upstream(self.route(), self.request(), 180)
            self.assertEqual(opened.return_value.open.call_count, 1)
            body.close.assert_called_once()

    def test_5xx_capacity_message_is_not_a_no_task_receipt(self):
        error = urllib.error.HTTPError("https://example.invalid", 503, "unavailable", {}, io.BytesIO(b'{"error":{"message":"temporarily unavailable"}}'))
        with mock.patch.object(app.urllib.request, "build_opener") as opened:
            opened.return_value.open.side_effect = error
            with self.assertRaises(app.AdapterError) as raised:
                app.call_upstream(self.route(), self.request(), 180)
            self.assertNotIsInstance(raised.exception, app.UpstreamRejected)

    def test_existing_task_identifier_overrides_quota_rejection_marker(self):
        error = urllib.error.HTTPError("https://example.invalid", 429, "rejection", {}, io.BytesIO(b'{"task_id":"original-task-id","error":{"message":"insufficient quota"}}'))
        with mock.patch.object(app.urllib.request, "build_opener") as opened:
            opened.return_value.open.side_effect = error
            with self.assertRaises(app.AdapterError) as raised:
                app.call_upstream(self.route(), self.request(), 180)
            self.assertNotIsInstance(raised.exception, app.UpstreamRejected)

    def test_generated_canvas_intent_is_not_advertised_as_verified_pixels(self):
        caps = app.image_capabilities()
        self.assertEqual(caps["endpoints"], ["/v1/images/generations"])
        self.assertEqual(caps["image_count"], [1])
        self.assertFalse(caps["image_edit"])
        self.assertFalse(caps["canvas_size_intent"]["verified_output_dimensions"])
        self.assertEqual(caps["canvas_size_intent"]["enforcement"], "prompt_only")

    def test_local_concurrency_rejection_is_no_task_not_provider_uncertainty(self):
        config = app.Config("127.0.0.1", 0, "fixture", 180, 1, {"banana-flash": (self.route(),)})
        runtime = app.Runtime(config)
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.handler_class(runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.object(runtime, "start_request", return_value=False), mock.patch.object(app, "call_upstream") as upstream, mock.patch("sys.stdout", new=io.StringIO()):
                client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                client.request("POST", "/v1/images/generations", body=json.dumps({"model": "banana-flash", "prompt": "fixture"}), headers={"Authorization": "Bearer fixture", "Content-Type": "application/json"})
                response = client.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 429)
                self.assertEqual(body["error"]["code"], "upstream_rejected_no_task")
                self.assertIn("upstream_rejected_no_task", body["error"]["message"])
                upstream.assert_not_called()
                client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
