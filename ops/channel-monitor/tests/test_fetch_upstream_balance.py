import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch-upstream-balance.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("fetch_upstream_balance", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class UsageV1AggregationTests(unittest.TestCase):
    def test_ledger_writer_marks_temporary_file_private_before_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ledger.json"
            path.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(collector.os, "chmod") as chmod,
                mock.patch.object(collector.os, "geteuid", return_value=0, create=True),
                mock.patch.object(collector.os, "chown", create=True) as chown,
            ):
                collector.write_json(path, {"days": {}})

        chmod.assert_called_once_with(pathlib.Path(str(path) + ".tmp"), 0o600)
        chown.assert_called_once()

    def test_balance_probe_reads_classic_self_without_logs_or_pricing(self):
        with (
            mock.patch.object(collector.requests, "Session"),
            mock.patch.object(collector, "standard_login", return_value="default"),
            mock.patch.object(
                collector,
                "standard_self",
                return_value={"quota": 250_000, "used_quota": 750_000},
            ),
            mock.patch.object(collector, "standard_logs") as logs,
            mock.patch.object(collector, "standard_pricing_metadata") as pricing,
        ):
            result = collector.probe_balance(
                "paisio",
                {
                    "username": "account-name",
                    "password": "secret",
                    "website_url": "https://example.test",
                    "rate": 1,
                },
                "",
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["balance_usd"], 0.5)
        self.assertEqual(result["billing_api"], "newapi_classic")
        logs.assert_not_called()
        pricing.assert_not_called()

    def test_balance_probe_falls_back_to_v1_and_accepts_zero(self):
        with (
            mock.patch.object(collector.requests, "Session"),
            mock.patch.object(
                collector, "standard_login", side_effect=RuntimeError("unsupported")
            ),
            mock.patch.object(collector, "v1_login"),
            mock.patch.object(collector, "v1_self", return_value={"balance": 0}),
            mock.patch.object(collector, "v1_logs") as logs,
        ):
            result = collector.probe_balance(
                "v1-provider",
                {
                    "username": "account-name",
                    "password": "secret",
                    "website_url": "https://example.test",
                    "rate": 1,
                },
                "",
            )

        self.assertEqual(result["balance_usd"], 0.0)
        self.assertEqual(result["billing_api"], "usage_v1")
        logs.assert_not_called()

    def test_balance_probe_rejects_missing_balance_instead_of_using_zero(self):
        with (
            mock.patch.object(collector.requests, "Session"),
            mock.patch.object(collector, "standard_login"),
            mock.patch.object(collector, "standard_self", return_value={}),
            mock.patch.object(collector, "v1_login"),
            mock.patch.object(collector, "v1_self", return_value={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "balance unavailable"):
                collector.probe_balance(
                    "missing",
                    {
                        "username": "account-name",
                        "password": "secret",
                        "website_url": "https://example.test",
                    },
                    "",
                )

    def test_toonflow_balance_probe_only_reads_points_preview(self):
        session = mock.Mock()
        session.headers = {}
        with (
            mock.patch.object(collector.requests, "Session", return_value=session),
            mock.patch.object(collector, "_toonflow_token", return_value="operator-token"),
            mock.patch.object(
                collector,
                "_toonflow_json_get",
                return_value={"data": {"totalPoints": "2.25"}},
            ) as get_json,
            mock.patch.object(collector, "toonflow_operation_logs") as operation_logs,
        ):
            result = collector.probe_balance(
                "toonflow",
                {"website_url": "https://api.toonflow.net", "rate": 1},
                "",
            )

        self.assertEqual(result["balance_usd"], 2.25)
        self.assertEqual(result["billing_api"], "toonflow_web")
        self.assertIn("pointsPreview/getPreviewData", get_json.call_args.args[1])
        operation_logs.assert_not_called()

    def test_classic_login_accepts_nested_user_and_bearer_token(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "success": True,
            "data": {
                "access_token": "temporary-token",
                "user": {"id": 9, "group": "default"},
            },
        }
        session = mock.Mock()
        session.post.return_value = response
        session.headers = {}

        group = collector.standard_login(
            session, "https://example.test", "user", "password"
        )

        self.assertEqual(group, "default")
        self.assertEqual(session.headers["New-Api-User"], "9")
        self.assertEqual(session.headers["Authorization"], "Bearer temporary-token")

    def test_authenticated_pricing_metadata_is_sanitized(self):
        session = mock.Mock()
        pricing_response = mock.Mock(status_code=200)
        pricing_response.json.return_value = {
            "success": True,
            "pricing_version": "v1",
            "group_ratio": {"default": 1},
            "data": [{
                "model_name": "video-pro-720p",
                "model_ratio": 2.5,
                "completion_ratio": 1,
                "enable_groups": ["default"],
                "billing_mode": "ratio",
                "billing_expr": "",
                "model_price": 2.5,
                "quota_type": 1,
                "secret_internal_field": "must-not-persist",
            }],
        }
        account_models_response = mock.Mock(status_code=200)
        account_models_response.json.return_value = {
            "success": True,
            "data": ["video-pro-720p", {"id": "gpt-5.6-sol"}],
        }
        session.get.side_effect = [pricing_response, account_models_response]

        metadata = collector.standard_pricing_metadata(session, "https://example.test")

        self.assertEqual(metadata["status"], "complete")
        self.assertEqual(metadata["models"][0]["model_name"], "video-pro-720p")
        self.assertEqual(metadata["models"][0]["model_price"], 2.5)
        self.assertEqual(metadata["models"][0]["quota_type"], 1)
        self.assertEqual(metadata["account_models"], ["gpt-5.6-sol", "video-pro-720p"])
        self.assertNotIn("secret_internal_field", metadata["models"][0])

    def test_account_model_metadata_failure_is_fail_closed(self):
        pricing_response = mock.Mock(status_code=200)
        pricing_response.json.return_value = {"success": True, "data": []}
        models_response = mock.Mock(status_code=401)
        models_response.json.return_value = {"success": False, "message": "unauthorized"}
        session = mock.Mock()
        session.get.side_effect = [pricing_response, models_response]

        with self.assertRaisesRegex(RuntimeError, "account models failed"):
            collector.standard_pricing_metadata(session, "https://example.test")

    def test_complete_entry_preserves_pricing_metadata(self):
        metadata = {"status": "complete", "models": [{"model_name": "gpt-5.6-sol"}]}
        entry = collector.complete_entry(
            "newapi_classic", 1.0, "default", 5.0, 1.0, 0.0, 0, {}, {}, None,
            pricing_metadata=metadata,
        )

        self.assertEqual(entry["pricing_metadata"], metadata)

    def test_metadata_failure_preserves_complete_actual_cost_and_redacts_secret(self):
        with (
            mock.patch.object(collector.requests, "Session"),
            mock.patch.object(collector, "standard_login", return_value="default"),
            mock.patch.object(
                collector,
                "standard_self",
                return_value={"quota": 5_000_000, "used_quota": 1_000_000},
            ),
            mock.patch.object(
                collector,
                "standard_logs",
                return_value=(1, 500_000, {"gpt-5.6-sol": 500_000}, {}),
            ),
            mock.patch.object(
                collector,
                "standard_pricing_metadata",
                side_effect=RuntimeError("upstream echoed super-secret"),
            ),
        ):
            entry = collector.collect_one(
                "paisio",
                {
                    "username": "account-name",
                    "password": "super-secret",
                    "website_url": "https://example.test",
                    "rate": 1,
                },
                "",
                {"days": {}},
                "2026-07-22",
            )

        self.assertEqual(entry["collection_status"], "complete")
        self.assertEqual(entry["day_log_cost_cny"], 1.0)
        self.assertEqual(entry["pricing_metadata"]["status"], "unavailable")
        self.assertNotIn("super-secret", entry["pricing_metadata"]["error"])

    def test_uses_account_total_cost_not_internal_actual_cost(self):
        total, per_model, real = collector.aggregate_v1_rows(
            [
                {
                    "model": "gpt-test",
                    "total_cost": 0.75,
                    "actual_cost": 0.10,
                    "input_cost": 0.50,
                    "output_cost": 0.25,
                    "input_tokens": 100_000,
                    "output_tokens": 10_000,
                }
            ],
            rate=1.0,
        )

        self.assertEqual(total, 0.75)
        self.assertEqual(per_model["gpt-test"], 0.75)
        self.assertEqual(real["gpt-test"]["input_cost_cny_per_m"], 5.0)
        self.assertEqual(real["gpt-test"]["output_cost_cny_per_m"], 25.0)

    def test_successful_empty_query_is_complete_zero(self):
        entry = collector.complete_entry(
            "usage_v1", 1.0, "", 5.0, None, 0.0, 0, {}, {}, None
        )

        self.assertEqual(entry["collection_status"], "complete")
        self.assertTrue(entry["actual_log_complete"])
        self.assertEqual(entry["day_log_cost_cny"], 0.0)
        self.assertEqual(entry["day_log_rows"], 0)

    def test_failure_is_null_and_preserves_no_fake_zero(self):
        entry = collector.failed_entry(None, "captcha required")

        self.assertEqual(entry["collection_status"], "incomplete")
        self.assertFalse(entry["actual_log_complete"])
        self.assertIsNone(entry["day_log_cost_cny"])
        self.assertIsNone(entry["day_log_rows"])

    def test_failed_retry_preserves_previous_complete_collection(self):
        prior = collector.complete_entry(
            "newapi_classic", 1.0, "", 10.0, 2.0, 1.25, 3, {}, {}, None
        )
        entry = collector.failed_entry(prior, "temporary timeout")

        self.assertEqual(entry["collection_status"], "complete")
        self.assertEqual(entry["day_log_cost_cny"], 1.25)
        self.assertEqual(entry["last_attempt_status"], "incomplete")

    def test_error_sanitizer_redacts_credentials(self):
        self.assertEqual(
            collector.clean_error("login alice@example.test secret", ("alice@example.test", "secret")),
            "login [redacted] [redacted]",
        )

    def test_collector_refuses_to_send_credentials_over_http(self):
        with self.assertRaisesRegex(RuntimeError, "non-HTTPS"):
            collector.collect_one(
                "unsafe",
                {"username": "user", "password": "secret", "website_url": "http://example.test"},
                "",
                {"days": {}},
                "2026-07-22",
            )

    def test_toonflow_collection_requires_operator_authorized_web_token(self):
        with self.assertRaisesRegex(RuntimeError, "web token"):
            collector.collect_one(
                "toonflow",
                {
                    "username": "user",
                    "password": "secret",
                    "website_url": "https://api.toonflow.net",
                    "rate": 1,
                },
                "",
                {"days": {}},
                "2026-08-09",
            )

    def test_newapi_task_api_preserves_video_task_evidence(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "success": True,
            "data": {
                "items": [
                    {
                        "task_id": "paisio-task",
                        "action": "videoGenerate",
                        "status": "SUCCESS",
                        "quota": 500000,
                        "created_at": 1786291199,
                    }
                ],
                "total": 1,
            },
        }
        session = mock.Mock()
        session.get.return_value = response

        rows = collector.standard_video_tasks(
            session, "https://example.test", "2026-08-09", "paisio", 1.0
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider_task_id"], "paisio-task")
        self.assertEqual(rows[0]["actual_cost_cny"], 1.0)

    def test_newapi_task_api_rejects_short_page_before_reported_total(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "success": True,
            "data": {"items": [{"task_id": "one"}], "total": 200},
        }
        session = mock.Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "pagination incomplete"):
            collector.standard_video_tasks(
                session, "https://example.test", "2026-08-09", "paisio", 1.0
            )

    def test_toonflow_operation_log_uses_bearer_token_and_sanitized_cost(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "code": 200,
            "data": {
                "list": [
                    {
                        "taskICode": "toon-task",
                        "modelName": "seedance-2.0-full-720p",
                        "state": 2,
                        "price": 1.5,
                        "creationTime": "2026-08-09 20:00:00",
                    }
                ],
                "total": 1,
            },
        }
        session = mock.Mock()
        session.headers = {}
        session.get.return_value = response

        rows = collector.toonflow_operation_logs(
            session,
            "https://api.toonflow.net",
            "operator-token",
            "2026-08-09",
            1.0,
        )

        self.assertEqual(session.headers["Authorization"], "Bearer operator-token")
        self.assertEqual(rows[0]["provider_task_id"], "toon-task")
        self.assertEqual(rows[0]["actual_cost_cny"], 1.5)
        self.assertNotIn("operator-token", str(rows))

    def test_toonflow_operation_log_accepts_current_nested_data_list(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "code": 200,
            "data": {
                "data": [
                    {
                        "taskICode": "toon-current-shape",
                        "modelName": "seedance-2.0-full-720p",
                        "state": 2,
                        "price": 2.25,
                        "creationTime": "2026-08-09 20:00:00",
                    }
                ],
                "total": 1,
            },
        }
        session = mock.Mock()
        session.headers = {}
        session.get.return_value = response

        rows = collector.toonflow_operation_logs(
            session,
            "https://api.toonflow.net",
            "operator-token",
            "2026-08-09",
            1.0,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider_task_id"], "toon-current-shape")
        self.assertEqual(rows[0]["actual_cost_cny"], 2.25)

    def test_toonflow_rejects_short_page_before_reported_total(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "code": 200,
            "data": {"list": [{"taskICode": "one"}], "total": 200},
        }
        session = mock.Mock()
        session.headers = {}
        session.get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "pagination incomplete"):
            collector.toonflow_operation_logs(
                session, "https://api.toonflow.net", "operator-token", "2026-08-09", 1.0
            )

    def test_toonflow_token_is_never_sent_to_unapproved_host(self):
        with self.assertRaisesRegex(RuntimeError, "unapproved host"):
            collector.collect_toonflow(
                {
                    "web_token": "operator-token",
                    "website_url": "https://evil.example",
                    "rate": 1,
                },
                "",
                {"days": {}},
                "2026-08-09",
            )

    def test_task_cost_evidence_fails_closed_when_it_disagrees_with_billing_log(self):
        rows, status = collector.validate_video_task_evidence(
            [
                {
                    "provider_task_id": "task-1",
                    "actual_cost_cny": 3.0,
                    "actual_cost_status": "actual",
                }
            ],
            expected_cost_cny=1.0,
        )

        self.assertEqual(status, "cost_mismatch")
        self.assertIsNone(rows[0]["actual_cost_cny"])
        self.assertEqual(rows[0]["actual_cost_status"], "unknown")

    def test_per_second_video_refunds_are_not_averaged_over_log_rows(self):
        samples = []
        for duration, count in ((4, 20), (5, 7), (12, 4)):
            samples.extend(
                {
                    "billing_type": "per_sec",
                    "duration": duration,
                    "price_per_sec": 0.29,
                    "price_per_call": 0,
                    "quota": duration * 0.29 * collector.QUOTA_PER_USD,
                }
                for _ in range(count)
            )
        for duration, count in ((4, 16), (5, 3), (12, 4)):
            samples.extend(
                {
                    "billing_type": "generation_failed_refund",
                    "duration": duration,
                    "price_per_sec": 0.29,
                    "price_per_call": 0,
                    "quota": -duration * 0.29 * collector.QUOTA_PER_USD,
                }
                for _ in range(count)
            )
        samples.extend(
            {
                "billing_type": "completed",
                "duration": duration,
                "price_per_sec": 0.29,
                "price_per_call": 0,
                "quota": 0,
            }
            for duration in (4, 4, 4, 4, 5, 5, 5, 5)
        )
        detail = {
            "calls": len(samples),
            "sum_quota": 5_220_000,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "pricing_samples": samples,
        }

        result = collector.classic_model_real_costs({"sd2-720p": detail}, rate=1)

        self.assertEqual(result["sd2-720p"]["kind"], "video")
        self.assertEqual(result["sd2-720p"]["billing_unit"], "second")
        self.assertEqual(result["sd2-720p"]["cost_cny_per_second"], 0.29)
        self.assertEqual(result["sd2-720p"]["successful_calls"], 8)
        self.assertEqual(result["sd2-720p"]["successful_output_seconds"], 36)
        self.assertEqual(result["sd2-720p"]["net_cost_cny"], 10.44)
        self.assertNotIn("cost_cny_per_call", result["sd2-720p"])

    def test_per_call_video_refunds_are_reconstructed_by_successful_tasks(self):
        samples = []
        samples.extend(
            {
                "billing_type": "per_call",
                "price_per_call": 2.5,
                "quota": 1_250_000,
            }
            for _ in range(3)
        )
        samples.append(
            {
                "billing_type": "generation_failed_refund",
                "price_per_call": 2.5,
                "quota": -1_250_000,
            }
        )
        samples.extend(
            {
                "billing_type": "completed",
                "price_per_call": 2.5,
                "quota": 0,
            }
            for _ in range(2)
        )
        details = {
            "seedance-2.0-720p": {
                "calls": len(samples),
                "sum_quota": 2_500_000,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "pricing_samples": samples,
            }
        }

        cost = collector.classic_model_real_costs(details, 1)["seedance-2.0-720p"]

        self.assertEqual(cost["kind"], "video")
        self.assertEqual(cost["billing_unit"], "call")
        self.assertEqual(cost["successful_calls"], 2)
        self.assertEqual(cost["net_cost_cny"], 5.0)
        self.assertEqual(cost["cost_cny_per_call"], 2.5)


if __name__ == "__main__":
    unittest.main()
