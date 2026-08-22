import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "scan-upstream-daily.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("scan_upstream_daily", SCRIPT_PATH)
scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan)


class ScanTransportTests(unittest.TestCase):
    def test_disabled_channel_invalid_mapping_is_not_parsed(self):
        row = scan.base_channel_record(
            {
                "id": 99,
                "name": "disabled",
                "status": 2,
                "models": "gpt-test",
                "model_mapping": "{bad json",
            },
            {"slug": "disabled"},
        )

        self.assertEqual(row["status"], 2)
        self.assertEqual(row["configured_models"], [])
        self.assertEqual(row["model_mapping"], {})

    def test_sensitive_probe_refuses_non_https_before_open(self):
        with mock.patch.object(scan.request, "build_opener") as build:
            status, payload, body, _ = scan.http_json(
                "http://example.test/v1/models",
                headers={"Authorization": "Bearer secret-value"},
            )

        self.assertIsNone(status)
        self.assertIsNone(payload)
        self.assertIn("non-HTTPS", body)
        build.assert_not_called()

    def test_transport_error_redacts_authorization_material(self):
        opener = mock.Mock()
        opener.open.side_effect = RuntimeError("failed secret-value")
        with mock.patch.object(scan.request, "build_opener", return_value=opener):
            _, _, body, _ = scan.http_json(
                "https://example.test/v1/models",
                headers={"Authorization": "Bearer secret-value"},
            )

        self.assertNotIn("secret-value", body)
        self.assertIn("[redacted]", body)

    def test_redirect_handler_never_forwards_request(self):
        handler = scan.NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "redirect", {}, "https://other.test")
        )


if __name__ == "__main__":
    unittest.main()
