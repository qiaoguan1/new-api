import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_loads_file_backed_secrets_and_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (
                ("adapter", "adapter-secret"),
                ("haina", "haina-secret"),
                ("rolldek", "rolldek-secret"),
            ):
                path = root / name
                path.write_text(value, encoding="utf-8")
                path.chmod(0o600)
            environment = {
                "BANANA_ADAPTER_TOKEN_FILE": str(root / "adapter"),
                "BANANA_HAINA_KEY_FILE": str(root / "haina"),
                "BANANA_ROLLDEK_KEY_FILE": str(root / "rolldek"),
                "BANANA_HAINA_BASE_URL": "https://ai.0809.one/v1",
                "BANANA_ROLLDEK_BASE_URL": "https://rolldek.com/v1",
            }

            config = app.Config.from_env(environment)

            self.assertEqual(config.adapter_token, "adapter-secret")
            self.assertEqual(
                [route.provider for route in config.routes["banana-flash"]],
                ["haina", "rolldek"],
            )
            self.assertEqual(
                [route.upstream_model for route in config.routes["banana-pro"]],
                ["gemini-3-pro-image-preview"],
            )

    def test_rejects_world_readable_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("secret", encoding="utf-8")
            path.chmod(0o644)
            environment = {
                "BANANA_ADAPTER_TOKEN_FILE": str(path),
                "BANANA_HAINA_KEY_FILE": str(path),
                "BANANA_ROLLDEK_KEY_FILE": str(path),
            }

            with mock.patch.object(app.os, "name", "posix"):
                with self.assertRaisesRegex(RuntimeError, "0600"):
                    app.Config.from_env(environment)


class RequestValidationTests(unittest.TestCase):
    def test_accepts_bounded_generation(self):
        request = app.validate_generation_request(
            {
                "model": "banana-flash",
                "prompt": "a blue circle",
                "size": "1024x1024",
                "n": 1,
                "response_format": "url",
            }
        )

        self.assertEqual(request.model, "banana-flash")
        self.assertEqual(request.size, "1024x1024")

    def test_rejects_unsupported_model_count_format_and_dimensions(self):
        invalid = (
            ({"model": "other", "prompt": "x"}, "unsupported_model"),
            ({"model": "banana-flash", "prompt": "x", "n": 2}, "invalid_n"),
            (
                {"model": "banana-flash", "prompt": "x", "response_format": "b64_json"},
                "unsupported_response_format",
            ),
            ({"model": "banana-flash", "prompt": "x", "size": "9000x9000"}, "invalid_size"),
        )
        for payload, code in invalid:
            with self.subTest(code=code):
                with self.assertRaises(app.AdapterError) as raised:
                    app.validate_generation_request(payload)
                self.assertEqual(raised.exception.code, code)

    def test_rejects_invalid_content_lengths(self):
        for value in ("", "not-a-number", "-1", str(app.MAX_REQUEST_BYTES + 1)):
            with self.subTest(value=value):
                with self.assertRaises(app.AdapterError) as raised:
                    app.validate_content_length(value)
                self.assertEqual(raised.exception.code, "invalid_request_size")


class AuthorizationTests(unittest.TestCase):
    def test_requires_exact_bearer_token(self):
        self.assertTrue(app.authorized("Bearer internal-secret", "internal-secret"))
        self.assertFalse(app.authorized("bearer internal-secret", "internal-secret"))
        self.assertFalse(app.authorized("Bearer wrong", "internal-secret"))
        self.assertFalse(app.authorized("", "internal-secret"))


class ResponseParsingTests(unittest.TestCase):
    def test_extracts_markdown_https_image(self):
        parsed = app.extract_image_references("Created: ![image](https://cdn.example/a.png)")
        self.assertEqual(parsed, ["https://cdn.example/a.png"])

    def test_extracts_data_uri_without_decoding_or_logging_it(self):
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        parsed = app.extract_image_references(f"data:image/png;base64,{encoded}")
        self.assertEqual(parsed, [f"data:image/png;base64,{encoded}"])

    def test_rejects_text_only_success(self):
        with self.assertRaisesRegex(app.AdapterError, "no image"):
            app.extract_image_references("I could not create an image")

    def test_builds_openai_image_response(self):
        response = app.image_response(
            app.UpstreamResult("haina", ["https://cdn.example/a.png"], 200, 1.0)
        )
        self.assertEqual(response["data"], [{"url": "https://cdn.example/a.png"}])

    def test_masks_raw_upstream_rejection_message(self):
        error = app.UpstreamRejected("haina", 503, "secret provider detail", 0.2)
        response = app.error_response(error)
        self.assertEqual(response["error"]["message"], "upstream rejected the request")
        self.assertNotIn("secret provider detail", json.dumps(response))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.routes = {
            "banana-flash": (
                app.Route("haina", "https://haina.example/v1", "key-a", "flash"),
                app.Route("rolldek", "https://rolldek.example/v1", "key-b", "flash"),
            ),
            "banana-pro": (
                app.Route("rolldek", "https://rolldek.example/v1", "key-b", "pro"),
            ),
        }

    def test_flash_uses_haina_without_calling_backup_after_success(self):
        caller = mock.Mock(return_value=app.UpstreamResult("haina", ["https://cdn.example/a.png"], 200, 1.0))
        result = app.generate_with_routes(
            app.GenerationRequest("banana-flash", "prompt", "1024x1024"),
            self.routes,
            caller,
        )
        self.assertEqual(result.provider, "haina")
        caller.assert_called_once()

    def test_flash_falls_back_only_after_explicit_safe_rejection(self):
        caller = mock.Mock(
            side_effect=(
                app.UpstreamRejected("haina", 503, "No available channel", 0.5),
                app.UpstreamResult("rolldek", ["https://cdn.example/b.png"], 200, 20.0),
            )
        )
        result = app.generate_with_routes(
            app.GenerationRequest("banana-flash", "prompt", "1024x1024"),
            self.routes,
            caller,
        )
        self.assertEqual(result.provider, "rolldek")
        self.assertEqual(caller.call_count, 2)

    def test_does_not_retry_ambiguous_timeout_or_empty_success(self):
        for error in (
            app.UpstreamUncertain("haina", "timeout"),
            app.AdapterError(502, "upstream_image_empty", "upstream returned no image"),
        ):
            caller = mock.Mock(side_effect=error)
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    app.generate_with_routes(
                        app.GenerationRequest("banana-flash", "prompt", "1024x1024"),
                        self.routes,
                        caller,
                    )
                caller.assert_called_once()

    def test_pro_has_no_automatic_fallback(self):
        caller = mock.Mock(side_effect=app.UpstreamRejected("rolldek", 503, "pool unavailable", 0.3))
        with self.assertRaises(app.UpstreamRejected):
            app.generate_with_routes(
                app.GenerationRequest("banana-pro", "prompt", "1024x1024"),
                self.routes,
                caller,
            )
        caller.assert_called_once()


if __name__ == "__main__":
    unittest.main()
