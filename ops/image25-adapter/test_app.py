"""No-charge contracts for the captured legacy Image2.5 adapter."""

import http.client
import importlib.util
import json
import pathlib
import sys
import threading
import unittest
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

    def request(self, provider, path="/v1/images/generations", body=None, method="POST"):
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port)
            client.request(method, "/" + provider + path, body=json.dumps(body) if body is not None else None, headers={"Authorization": "Bearer fixture", "Content-Type": "application/json"})
            response = client.getresponse()
            value = response.status, response.getheader("X-XingTu-Image-Submission-State"), json.loads(response.read())
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


if __name__ == "__main__":
    unittest.main()
