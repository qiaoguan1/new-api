import importlib.util
import json
import pathlib
import sys
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "refresh-video-provider-auth.py"
spec = importlib.util.spec_from_file_location("refresh_video_provider_auth", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class Response:
    status = 200

    def __init__(self, value):
        self.body = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


class Opener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if request.get_method() == "POST":
            return Response(
                {
                    "success": True,
                    "data": {"access_token": "read-only-session-token", "user": {"id": 42}},
                }
            )
        return Response({"success": True, "data": {"items": [], "total": 0}})


class RefreshVideoProviderAuthTests(unittest.TestCase):
    def test_refreshes_and_verifies_newapi_read_only_session(self):
        opener = Opener()
        document = module.refresh_newapi_session(
            {
                "provider_id": "paisio",
                "base_url": "https://api.paisio.online",
                "lease_seconds": 7200,
            },
            {"username": "account", "password": "password"},
            now=2_000_000_000,
            opener=opener,
        )

        self.assertEqual(document["provider_id"], "paisio")
        self.assertEqual(document["new_api_user"], "42")
        self.assertEqual(document["authorization"], "Bearer read-only-session-token")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(opener.requests[1].get_method(), "GET")
        self.assertIn("/api/task/self", opener.requests[1].full_url)

    def test_rejects_redirectable_or_credentialed_origin(self):
        for value in (
            "http://api.paisio.online",
            "https://user:pass@api.paisio.online",
            "https://api.paisio.online/path",
            "https://api.paisio.online?token=secret",
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                module.approved_origin(value)

    def test_refresh_failure_alert_is_deduplicated_until_recovery(self):
        config = {
            "providers": [
                {
                    "provider_id": "paisio",
                    "refresh_mode": "scheduled_login",
                    "credential_source_slug": "paisio",
                    "base_url": "https://api.paisio.online",
                    "output_file": "/not-used-in-dry-run",
                }
            ]
        }
        credentials = {"paisio": {"username": "account", "password": "password"}}
        state = {}
        with mock.patch.object(module, "refresh_newapi_session", side_effect=RuntimeError("secret body")):
            first = module.run(config, credentials, state, now=100, dry_run=True)
            second = module.run(config, credentials, state, now=200, dry_run=True)
        self.assertEqual([event["kind"] for event in first["events"]], ["credential_refresh_failed"])
        self.assertEqual(second["events"], [])
        self.assertNotIn("secret body", str(first))

        with mock.patch.object(module, "refresh_newapi_session", return_value={}):
            recovered = module.run(config, credentials, state, now=300, dry_run=True)
        self.assertEqual(
            [event["kind"] for event in recovered["events"]],
            ["credential_refresh_recovered"],
        )

    def test_legacy_notification_fallback_contains_only_safe_status(self):
        transport = mock.Mock()
        transport.AlertEvent.side_effect = lambda **values: values
        event = module._legacy_notification_event(
            transport,
            {
                "kind": "credential_expiring",
                "provider_id": "toonflow",
                "threshold_days": 30,
                "occurred_at": 100,
            },
        )
        self.assertEqual(event["kind"], "balance_collection_failed")
        self.assertIn("30", event["name"])
        self.assertNotIn("token", str(event).lower())


if __name__ == "__main__":
    unittest.main()
