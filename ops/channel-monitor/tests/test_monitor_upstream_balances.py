import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "monitor-upstream-balances.py"
)
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("monitor_upstream_balances", SCRIPT_PATH)
monitor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(monitor)


class AlertStateTests(unittest.TestCase):
    def test_zero_balance_alert_is_deduplicated_until_reminder(self):
        record = {}
        row = {"status": "complete", "balance_usd": 0.0}

        events, record = monitor.observe_provider(
            "paisio", "Paisio", row, record, threshold=0, now=100, reminder_seconds=3600
        )
        self.assertEqual([event.kind for event in events], ["balance_depleted"])

        record = monitor.record_delivery(record, events[0], now=100)
        events, record = monitor.observe_provider(
            "paisio", "Paisio", row, record, threshold=0, now=200, reminder_seconds=3600
        )
        self.assertEqual(events, [])

        events, _ = monitor.observe_provider(
            "paisio", "Paisio", row, record, threshold=0, now=3700, reminder_seconds=3600
        )
        self.assertEqual([event.kind for event in events], ["balance_depleted_reminder"])

    def test_unknown_balance_never_becomes_depleted(self):
        record = {}
        row = {"status": "unknown", "balance_usd": None}
        kinds = []
        for now in (100, 200, 300):
            events, record = monitor.observe_provider(
                "broken", "Broken", row, record, threshold=0, now=now,
                failure_threshold=3,
            )
            kinds.extend(event.kind for event in events)

        self.assertEqual(kinds, ["balance_collection_failed"])
        self.assertFalse(record.get("depletion_open", False))

    def test_recovery_is_sent_once_after_delivered_depletion(self):
        depleted = {"status": "complete", "balance_usd": -0.01}
        healthy = {"status": "complete", "balance_usd": 2.0}
        events, record = monitor.observe_provider(
            "aihua", "aihua", depleted, {}, threshold=0, now=100
        )
        record = monitor.record_delivery(record, events[0], now=100)

        events, record = monitor.observe_provider(
            "aihua", "aihua", healthy, record, threshold=0, now=200
        )
        self.assertEqual([event.kind for event in events], ["balance_recovered"])
        record = monitor.record_delivery(record, events[0], now=200)

        events, _ = monitor.observe_provider(
            "aihua", "aihua", healthy, record, threshold=0, now=300
        )
        self.assertEqual(events, [])

    def test_failed_delivery_remains_pending(self):
        row = {"status": "complete", "balance_usd": 0}
        events, record = monitor.observe_provider(
            "hanhe", "寒鹤", row, {}, threshold=0, now=100
        )

        events_again, _ = monitor.observe_provider(
            "hanhe", "寒鹤", row, record, threshold=0, now=200
        )

        self.assertEqual(events[0].kind, "balance_depleted")
        self.assertEqual(events_again[0].kind, "balance_depleted")

    def test_monitor_recovery_is_not_mislabeled_as_balance_recovery(self):
        unknown = {"status": "unknown", "balance_usd": None}
        record = {}
        for now in (100, 200, 300):
            events, record = monitor.observe_provider(
                "upstream", "Upstream", unknown, record, threshold=0, now=now,
                failure_threshold=3,
            )
        record = monitor.record_delivery(record, events[0], now=300)

        events, _ = monitor.observe_provider(
            "upstream", "Upstream", {"status": "complete", "balance_usd": 1},
            record, threshold=0, now=400,
        )

        self.assertEqual([event.kind for event in events], ["balance_collection_recovered"])


class ConfigurationAndNotificationTests(unittest.TestCase):
    def test_notification_route_is_root_only_and_rate_limited(self):
        router_source = (
            pathlib.Path(__file__).resolve().parents[3] / "router" / "api-router.go"
        ).read_text(encoding="utf-8")
        option_block = router_source.split('optionRoute := apiRouter.Group("/option")', 1)[1]
        option_block = option_block.split("// Custom OAuth provider management", 1)[0]

        self.assertIn("optionRoute.Use(middleware.RootAuth())", option_block)
        self.assertIn(
            'optionRoute.POST("/upstream_balance_alert", middleware.CriticalRateLimit(), controller.SendUpstreamBalanceAlert)',
            option_block,
        )
        self.assertIn(
            'optionRoute.POST("/upstream_ops_digest", middleware.CriticalRateLimit(), controller.SendUpstreamOpsDigest)',
            option_block,
        )

    def test_only_enabled_credentialed_upstreams_are_selected(self):
        selected = monitor.select_targets(
            [
                {"slug": "enabled", "name": "Enabled", "website_url": "https://one.test"},
                {"slug": "disabled", "name": "Disabled", "enabled": False},
                {"slug": "missing", "name": "Missing"},
            ],
            {"enabled": {}, "disabled": {}, "orphan": {}},
        )

        self.assertEqual([item[0] for item in selected], ["enabled"])

    def test_access_token_file_must_be_private_on_posix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "newapi-token"
            path.write_text("root-access-token\n", encoding="utf-8")
            path.chmod(0o644)

            with self.assertRaisesRegex(RuntimeError, "0600"):
                monitor.read_token_file(path, platform_name="posix")

    def test_notification_url_rejects_external_plain_http(self):
        with self.assertRaisesRegex(ValueError, "notification URL"):
            monitor.validate_notify_url("http://public.example.com")
        with self.assertRaisesRegex(ValueError, "notification URL"):
            monitor.validate_notify_url("https://public.example.com")
        with self.assertRaisesRegex(ValueError, "notification URL"):
            monitor.validate_notify_url("http://127.0.0.1:3000/unexpected-prefix")

    def test_notification_request_uses_root_auth_and_never_contains_token(self):
        config = monitor.NotifyConfig(
            base_url="http://127.0.0.1:3000",
            token="root-access-token",
            user_id="1",
            timeout=12,
        )
        event = monitor.AlertEvent(
            kind="balance_depleted",
            slug="provider",
            name="Provider",
            balance=0,
            threshold=0,
            occurred_at=100,
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"success":true}'
        captured = {}

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

        opener = mock.Mock()
        opener.open.side_effect = fake_open
        with mock.patch.object(urllib.request, "build_opener", return_value=opener):
            monitor.send_event(config, event)

        request = captured["request"]
        self.assertEqual(request.get_header("Authorization"), "root-access-token")
        self.assertEqual(request.get_header("New-api-user"), "1")
        self.assertNotIn(b"root-access-token", request.data)
        self.assertNotIn(b"recipient", request.data)

    def test_notification_redirects_are_forbidden(self):
        handler = monitor.NoRedirectHandler()
        request = urllib.request.Request("http://127.0.0.1:3000/api/option/upstream_balance_alert")
        with self.assertRaisesRegex(Exception, "redirects are forbidden"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://attacker.example.com/steal"
            )

    def test_probe_failure_snapshot_never_contains_exception_or_credentials(self):
        with mock.patch.object(
            monitor.COLLECTOR,
            "probe_balance",
            side_effect=RuntimeError("login failed for alice secret-password"),
        ):
            snapshot = monitor.probe_targets(
                [
                    (
                        "one",
                        "One",
                        {"username": "alice", "password": "secret-password"},
                        "https://one.test",
                    )
                ],
                now=100,
            )

        serialized = str(snapshot)
        self.assertNotIn("alice", serialized)
        self.assertNotIn("secret-password", serialized)
        self.assertEqual(snapshot["providers"]["one"]["error_code"], "probe_failed")

    def test_private_json_writer_uses_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            with mock.patch.object(os, "replace", wraps=os.replace) as replace:
                monitor.write_private_json(path, {"schema_version": 1})

            replace.assert_called_once()
            self.assertEqual(
                __import__("json").loads(path.read_text(encoding="utf-8")),
                {"schema_version": 1},
            )
            self.assertFalse(path.with_name("state.json.tmp").exists())

    def test_dry_run_does_not_send_or_write_alert_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            upstreams = root / "upstreams.json"
            credentials = root / "credentials.json"
            snapshot = root / "snapshot.json"
            state = root / "state.json"
            upstreams.write_text(
                '[{"slug":"one","name":"One","website_url":"https://one.test"}]',
                encoding="utf-8",
            )
            credentials.write_text('{"one":{}}', encoding="utf-8")
            with (
                mock.patch.object(
                    monitor.COLLECTOR,
                    "probe_balance",
                    return_value={"status": "complete", "balance_usd": 0, "billing_api": "test"},
                ),
                mock.patch.object(monitor, "send_event") as send,
            ):
                code = monitor.main(
                    [
                        "--dry-run",
                        "--upstreams", str(upstreams),
                        "--credentials", str(credentials),
                        "--snapshot", str(snapshot),
                        "--state", str(state),
                    ],
                    environ={"UPSTREAM_BALANCE_ALERT_TO": "operator@example.com"},
                )

            self.assertEqual(code, 0)
            self.assertTrue(snapshot.exists())
            self.assertFalse(state.exists())
            send.assert_not_called()

    def test_missing_or_empty_required_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            snapshot = root / "snapshot.json"
            state = root / "state.json"

            code = monitor.main(
                [
                    "--dry-run",
                    "--upstreams", str(root / "missing-upstreams.json"),
                    "--credentials", str(root / "missing-credentials.json"),
                    "--snapshot", str(snapshot),
                    "--state", str(state),
                ],
                environ={},
            )

            self.assertEqual(code, 2)
            self.assertFalse(snapshot.exists())
            self.assertFalse(state.exists())

    def test_corrupt_existing_state_fails_without_resetting_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            upstreams = root / "upstreams.json"
            credentials = root / "credentials.json"
            snapshot = root / "snapshot.json"
            state = root / "state.json"
            upstreams.write_text(
                '[{"slug":"one","name":"One","website_url":"https://one.test"}]',
                encoding="utf-8",
            )
            credentials.write_text('{"one":{}}', encoding="utf-8")
            state.write_text("{broken", encoding="utf-8")
            with mock.patch.object(
                monitor.COLLECTOR,
                "probe_balance",
                return_value={"status": "complete", "balance_usd": 0, "billing_api": "test"},
            ):
                code = monitor.main(
                    [
                        "--dry-run",
                        "--upstreams", str(upstreams),
                        "--credentials", str(credentials),
                        "--snapshot", str(snapshot),
                        "--state", str(state),
                    ],
                    environ={},
                )

            self.assertEqual(code, 2)
            self.assertEqual(state.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
