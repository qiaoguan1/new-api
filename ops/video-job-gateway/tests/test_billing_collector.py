import json
import os
import pathlib
import base64
import sys
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import Config, Gateway
from billing_collectors import (
    BillingCollectionError,
    BillingRecord,
    NewAPITaskBillingCollector,
    ToonflowBillingCollector,
)
from store import BILLING_CONTRACT_VERSION, Store


def priced_payload(amount: str = "5.961600") -> str:
    return (
        '{"model":"seedance-2.0","duration":4,"_billing_v2":true,'
        '"_billing_contract_version":"' + BILLING_CONTRACT_VERSION + '",'
        '"_relay_price":{"contract_version":"xtai-video-pricing-v1",'
        '"currency":"CNY","amount_cny_exact":"' + amount + '",'
        '"official_cost_cny_exact":"3.974400",'
        '"fallback_multiplier_exact":"1.5",'
        '"pricing_revision":"test-revision","price_source":"ark_official_1_5"}}'
    )


def completed_row(task_id: str = "cgt-test", **overrides):
    row = {
        "taskICode": task_id,
        "state": 2,
        "price": "0.5135141",
        "completionTime": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    }
    row.update(overrides)
    return row


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload, separators=(",", ":")).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        payload = self.payload(request) if callable(self.payload) else self.payload
        return FakeResponse(payload)


class ToonflowCollectorTests(unittest.TestCase):
    def collector(self, payload, *, token="separate-read-only-service-token"):
        opener = FakeOpener(payload)
        return ToonflowBillingCollector(
            "https://api.toonflow.net/web/web/operationLog/getOperationLog",
            token,
            opener=opener,
        ), opener

    def test_collects_exact_completed_task_with_six_decimal_ceiling(self):
        collector, opener = self.collector({"code": 0, "data": {"data": [completed_row()]}})

        record = collector.collect("cgt-test")

        self.assertEqual(record.actual_cost_cny_exact, "0.513515")
        self.assertEqual(record.actual_cost_status, "actual")
        self.assertEqual(record.evidence_source, "toonflow_web_operation_log")
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("taskICode=cgt-test", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer separate-read-only-service-token")
        self.assertEqual(timeout, 10)

    def test_zero_cost_requires_completed_authoritative_row(self):
        collector, _ = self.collector({"data": {"records": [completed_row(price="0")]}})
        record = collector.collect("cgt-test")
        self.assertEqual(record.actual_cost_status, "zero_verified")
        self.assertEqual(record.actual_cost_cny_exact, "0.000000")

    def test_failed_operation_collects_authoritative_terminal_price(self):
        collector, _ = self.collector({
            "code": 0,
            "data": {"data": [completed_row(state=-1, price="7.5", errorReason="provider failed")]},
        })

        record = collector.collect_failed("cgt-test")

        self.assertEqual(record.actual_cost_status, "actual")
        self.assertEqual(record.actual_cost_cny_exact, "7.500000")
        self.assertEqual(record.evidence_source, "toonflow_web_failed_operation_log")

    def test_token_file_is_reloaded_without_gateway_restart(self):
        def token(label):
            payload = base64.urlsafe_b64encode(
                json.dumps({"exp": 4_102_444_800, "label": label}).encode()
            ).decode().rstrip("=")
            return f"header.{payload}.signature"

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = pathlib.Path(directory) / "toonflow.token"
            path.write_text(token("first"), encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o600)
            opener = FakeOpener({"data": {"records": [completed_row()]}})
            collector = ToonflowBillingCollector(
                "https://api.toonflow.net/web/web/operationLog/getOperationLog",
                "",
                token_file=path,
                opener=opener,
            )
            self.assertTrue(collector.ready)
            collector.collect("cgt-test")
            self.assertEqual(opener.requests[-1][0].get_header("Authorization"), f"Bearer {token('first')}")

            path.write_text(token("second"), encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o600)
            collector.collect("cgt-test")
            self.assertEqual(opener.requests[-1][0].get_header("Authorization"), f"Bearer {token('second')}")

    def test_toonflow_epoch_milliseconds_are_normalized_to_beijing_time(self):
        collector, _ = self.collector(
            {"data": {"data": [completed_row(completionTime=1786502790531)]}}
        )

        record = collector.collect("cgt-test")

        self.assertEqual(record.observed_at, "2026-08-12T10:46:30+08:00")

    def test_unix_seconds_and_utc_iso_are_normalized_to_beijing_time(self):
        values = (
            1786502790,
            "2026-08-12T02:46:30Z",
        )
        for completion_time in values:
            with self.subTest(completion_time=completion_time):
                collector, _ = self.collector(
                    {"data": {"data": [completed_row(completionTime=completion_time)]}}
                )
                record = collector.collect("cgt-test")
                self.assertEqual(record.observed_at, "2026-08-12T10:46:30+08:00")

    def test_wrong_task_running_duplicate_and_bad_amount_fail_closed(self):
        cases = (
            ({"data": [completed_row("another-task")]}, "provider_billing_record_not_ready"),
            ({"data": [completed_row(state=1)]}, "provider_billing_record_not_final"),
            ({"data": [completed_row(), completed_row()]}, "provider_billing_record_ambiguous"),
            ({"data": [completed_row(price="not-money")]}, "provider_billing_amount_invalid"),
            ({"data": [completed_row(completionTime="")]}, "provider_billing_completion_time_missing"),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                collector, _ = self.collector(payload)
                with self.assertRaises(BillingCollectionError) as failure:
                    collector.collect("cgt-test")
                self.assertEqual(failure.exception.code, code)

    def test_authentication_failure_exposes_no_token_or_response_body(self):
        token = "highly-sensitive-read-only-token"
        error = urllib.error.HTTPError(
            "https://api.toonflow.net/web/web/operationLog/getOperationLog",
            401,
            "unauthorized secret response",
            {},
            None,
        )
        opener = FakeOpener(error=error)
        collector = ToonflowBillingCollector(
            "https://api.toonflow.net/web/web/operationLog/getOperationLog",
            token,
            opener=opener,
        )

        with self.assertRaises(BillingCollectionError) as failure:
            collector.collect("cgt-test")

        rendered = repr(failure.exception) + str(failure.exception)
        self.assertEqual(failure.exception.code, "provider_billing_authentication_failed")
        self.assertNotIn(token, rendered)
        self.assertNotIn("secret response", rendered)


class NewAPITaskBillingCollectorTests(unittest.TestCase):
    def collector(self, task_payload, *, ledger_payload=None, provider="paisio"):
        task_data = task_payload.get("data") if isinstance(task_payload, dict) else None
        if isinstance(task_data, dict) and "total" not in task_data:
            task_payload = dict(task_payload)
            task_payload["data"] = dict(task_data)
            task_payload["data"]["total"] = len(task_data.get("items") or [])

        def response_for(request):
            if "/api/log/self" in request.full_url:
                if ledger_payload is None:
                    raise AssertionError("unexpected billing-ledger request")
                return ledger_payload
            return task_payload

        opener = FakeOpener(response_for)
        return NewAPITaskBillingCollector(
            provider,
            f"https://{provider}.example/api/task/self",
            authorization="Bearer account-read-only-token",
            new_api_user="42",
            rate_cny_per_usd="1",
            opener=opener,
        ), opener

    def test_paisio_uses_exact_request_ledger_and_never_task_count_as_cost(self):
        collector, opener = self.collector(
            {
                "success": True,
                "data": {
                    "items": [
                        {
                            "task_id": "task-1",
                            "action": "generate_video",
                            "status": "SUCCESS",
                            "quota": 1,
                            "finish_time": "2026-08-12T02:46:30Z",
                        }
                    ]
                },
            },
            ledger_payload={
                "success": True,
                "data": {
                    "total": 2,
                    "items": [
                        {
                            "id": 11,
                            "type": 2,
                            "request_id": "task-1",
                            "quota": 256757.05,
                            "other": json.dumps({"billing_type": "per_sec"}),
                        },
                        {
                            "id": 12,
                            "type": 2,
                            "request_id": "task-1",
                            "quota": 0,
                            "other": json.dumps({"billing_type": "completed"}),
                        },
                    ],
                },
            },
        )

        record = collector.collect("task-1")

        self.assertEqual(record.actual_cost_cny_exact, "0.513515")
        self.assertEqual(record.evidence_source, "paisio_authenticated_request_ledger")
        task_request, _ = opener.requests[0]
        ledger_request, _ = opener.requests[1]
        self.assertIn("task_id=task-1", task_request.full_url)
        self.assertIn("request_id=task-1", ledger_request.full_url)
        self.assertEqual(ledger_request.get_header("Authorization"), "Bearer account-read-only-token")
        self.assertEqual(ledger_request.get_header("New-api-user"), "42")

    def test_paisio_failed_execution_id_resolves_billing_id_and_net_refund(self):
        collector, opener = self.collector(
            {
                "success": True,
                "data": {"items": [{
                    "id": 60149,
                    "task_id": "task_R6Failure",
                    "action": "videoGenerate",
                    "status": "FAILURE",
                    "submit_time": 1786600000,
                    "finish_time": 1786600100,
                    "data": {"task_id": "38b19213aa554916b2a9e3e3d57f5e47"},
                }]},
            },
            ledger_payload={
                "success": True,
                "data": {"total": 2, "items": [
                    {"id": 1, "type": 2, "request_id": "task_R6Failure", "quota": 460000,
                     "other": '{"billing_type":"per_sec"}'},
                    {"id": 2, "type": 2, "request_id": "task_R6Failure", "quota": -460000,
                     "other": '{"billing_type":"generation_failed_refund"}'},
                ]},
            },
        )

        record = collector.collect_failed("38b19213aa554916b2a9e3e3d57f5e47")

        self.assertEqual(record.provider_task_id, "task_R6Failure")
        self.assertEqual(record.execution_task_id, "38b19213aa554916b2a9e3e3d57f5e47")
        self.assertEqual(record.actual_cost_status, "zero_verified")
        self.assertEqual(record.actual_cost_cny_exact, "0.000000")
        self.assertIn("request_id=task_R6Failure", opener.requests[-1][0].full_url)

    def test_paisio_request_ledger_fails_closed_when_filter_is_ignored_or_refunded(self):
        task = {
            "success": True,
            "data": {"items": [{
                "task_id": "task-1",
                "action": "videoGenerate",
                "status": "SUCCESS",
                "quota": 1,
                "finish_time": 1786502790,
            }]},
        }
        cases = (
            (
                {"success": True, "data": {"total": 2, "items": [{
                    "type": 2,
                    "request_id": "another-task",
                    "quota": 500000,
                    "other": '{"billing_type":"per_sec"}',
                }]}},
                "provider_billing_ledger_incomplete",
            ),
            (
                {"success": True, "data": {"total": 2, "items": [
                    {"type": 2, "request_id": "task-1", "quota": 500000,
                     "other": '{"billing_type":"per_sec"}'},
                    {"type": 2, "request_id": "task-1", "quota": -500000,
                     "other": '{"billing_type":"generation_failed_refund"}'},
                ]}},
                "provider_billing_record_state_mismatch",
            ),
            (
                {"success": True, "data": {"total": 1, "items": [
                    {"type": 2, "request_id": "task-1", "quota": 500000,
                     "other": '{"billing_type":"per_sec"}'},
                ]}},
                "provider_billing_record_not_final",
            ),
            (
                {"success": True, "data": {"total": 2, "items": [
                    {"type": "invalid", "request_id": "task-1", "quota": 500000,
                     "other": '{"billing_type":"per_sec"}'},
                    {"type": 2, "request_id": "task-1", "quota": 0,
                     "other": '{"billing_type":"completed"}'},
                ]}},
                "provider_billing_response_invalid",
            ),
        )
        for ledger, code in cases:
            with self.subTest(code=code):
                collector, _ = self.collector(task, ledger_payload=ledger)
                with self.assertRaises(BillingCollectionError) as failure:
                    collector.collect("task-1")
                self.assertEqual(failure.exception.code, code)

    def test_paisio_task_lookup_must_be_an_exact_filtered_singleton(self):
        task = {
            "success": True,
            "data": {
                "total": 2,
                "items": [{
                    "task_id": "task-1",
                    "action": "videoGenerate",
                    "status": "SUCCESS",
                    "quota": 1,
                    "finish_time": 1786502790,
                }],
            },
        }
        collector, opener = self.collector(task)

        with self.assertRaises(BillingCollectionError) as failure:
            collector.collect("task-1")

        self.assertEqual(failure.exception.code, "provider_billing_ledger_incomplete")
        self.assertEqual(len(opener.requests), 1)

    def test_session_header_injection_is_rejected_before_network(self):
        collector = NewAPITaskBillingCollector(
            "paisio",
            "https://paisio.example/api/task/self",
            authorization="Bearer safe\r\nX-Evil: injected",
            new_api_user="42",
        )
        self.assertFalse(collector.ready)

    def test_failed_task_cannot_settle_a_succeeded_gateway_job(self):
        failed = {
            "task_id": "task-1",
            "action": "generate_video",
            "status": "FAILED",
            "quota": 999999,
            "updated_at": 1786502790,
        }
        collector, _ = self.collector({"success": True, "data": {"items": [failed]}})
        with self.assertRaises(BillingCollectionError) as state_mismatch:
            collector.collect("task-1")
        self.assertEqual(state_mismatch.exception.code, "provider_billing_record_state_mismatch")

        collector, _ = self.collector({"success": True, "data": {"items": [failed, failed]}})
        with self.assertRaises(BillingCollectionError) as failure:
            collector.collect("task-1")
        self.assertEqual(failure.exception.code, "provider_billing_record_ambiguous")

    def test_session_file_is_reloaded_and_expiry_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = pathlib.Path(directory) / "paisio-session.json"
            payload = {
                "provider_id": "paisio",
                "authorization": "Bearer first-token",
                "new_api_user": "42",
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o600)
            task_response = {
                    "success": True,
                    "data": {
                        "total": 1,
                        "items": [
                            {
                                "task_id": "task-1",
                                "action": "generate_video",
                                "status": "SUCCESS",
                                "quota": 1,
                                "finish_time": 1786502790,
                            }
                        ]
                    },
                }
            ledger_response = {
                "success": True,
                "data": {"total": 2, "items": [
                    {"type": 2, "request_id": "task-1", "quota": 500000,
                     "other": '{"billing_type":"per_sec"}'},
                    {"type": 2, "request_id": "task-1", "quota": 0,
                     "other": '{"billing_type":"completed"}'},
                ]},
            }
            opener = FakeOpener(
                lambda request: ledger_response
                if "/api/log/self" in request.full_url
                else task_response
            )
            collector = NewAPITaskBillingCollector(
                "paisio", "https://paisio.example/api/task/self", credential_file=path, opener=opener
            )
            self.assertTrue(collector.ready)
            collector.collect("task-1")
            self.assertEqual(opener.requests[-1][0].get_header("Authorization"), "Bearer first-token")

            payload["authorization"] = "Bearer rotated-token"
            path.write_text(json.dumps(payload), encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o600)
            collector.collect("task-1")
            self.assertEqual(opener.requests[-1][0].get_header("Authorization"), "Bearer rotated-token")

            payload["expires_at"] = "2020-01-01T00:00:00+00:00"
            path.write_text(json.dumps(payload), encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o600)
            self.assertFalse(collector.ready)

    def test_session_file_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = pathlib.Path(directory)
            target = root / "real-session.json"
            link = root / "paisio-session.json"
            target.write_text(
                json.dumps(
                    {
                        "provider_id": "paisio",
                        "authorization": "Bearer secret",
                        "new_api_user": "42",
                        "expires_at": "2099-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            collector = NewAPITaskBillingCollector(
                "paisio", "https://paisio.example/api/task/self", credential_file=link
            )
            self.assertFalse(collector.ready)

    def test_newapi_billing_provider_requires_private_session_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            base = {
                "VIDEO_JOB_GATEWAY_TOKEN": "gateway-test-token",
                "VIDEO_JOB_GATEWAY_DATA_DIR": directory,
                "VIDEO_JOB_PAISIO_BILLING_ENABLED": "1",
            }
            with mock.patch.dict(os.environ, base, clear=True):
                with self.assertRaisesRegex(RuntimeError, "credential file"):
                    Config.from_env()

            credential = pathlib.Path(directory) / "paisio-session.json"
            credential.write_text("{}", encoding="utf-8")
            enabled = {
                **base,
                "VIDEO_JOB_PAISIO_BILLING_CREDENTIAL_FILE": str(credential),
                "VIDEO_JOB_PAISIO_BILLING_RATE_CNY_PER_USD": "1",
            }
            with mock.patch.dict(os.environ, enabled, clear=True):
                config = Config.from_env()
            self.assertEqual(config.newapi_billing_enabled_providers, frozenset({"paisio"}))
            self.assertEqual(config.newapi_billing_credential_files["paisio"], credential.resolve())

            insecure = {
                **enabled,
                "VIDEO_JOB_PAISIO_BASE_URL": "http://api.paisio.online",
            }
            with mock.patch.dict(os.environ, insecure, clear=True):
                with self.assertRaisesRegex(RuntimeError, "HTTPS URL"):
                    Config.from_env()

    def test_configuration_is_default_off_and_requires_separate_safe_credentials(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            base = {
                "VIDEO_JOB_GATEWAY_TOKEN": "gateway-test-token",
                "VIDEO_JOB_GATEWAY_DATA_DIR": directory,
            }
            with mock.patch.dict(os.environ, base, clear=True):
                disabled = Config.from_env()
            self.assertFalse(disabled.toonflow_billing_enabled)
            self.assertEqual(disabled.toonflow_billing_token, "")

            enabled_env = {
                **base,
                "VIDEO_JOB_TOONFLOW_BILLING_ENABLED": "1",
                "VIDEO_JOB_TOONFLOW_BILLING_TOKEN": "separate-read-only-service-token",
            }
            with mock.patch.dict(os.environ, enabled_env, clear=True):
                enabled = Config.from_env()
            self.assertTrue(enabled.toonflow_billing_enabled)
            self.assertNotIn("separate-read-only-service-token", repr(enabled))

            with mock.patch.dict(
                os.environ,
                {**enabled_env, "VIDEO_JOB_TOONFLOW_BILLING_TOKEN": "short"},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "separate service token"):
                    Config.from_env()
            with mock.patch.dict(
                os.environ,
                {
                    **enabled_env,
                    "VIDEO_JOB_TOONFLOW_BILLING_LOG_URL": "https://example.com/operation-log",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "configured provider HTTPS domain"):
                    Config.from_env()


class SettlementCollectionWorkflowTests(unittest.TestCase):
    def make_pending(self, store: Store, *, request_id="collector-request") -> str:
        snapshot, _ = store.create(
            request_id=request_id,
            fingerprint="fingerprint-" + request_id,
            protocol_version="xtai-relay-v1",
            catalog_revision="test",
            stable_model="seedance-2.0",
            provider_id="toonflow",
            upstream_model="seedance-2.0",
            adapter_revision="toonflow-v1",
            payload_json=priced_payload(),
        )
        job_id = str(snapshot["job_id"])
        self.assertIsNotNone(store.claim_submit(job_id))
        store.mark_running(job_id, "cgt-test", "queued", 5)
        store.finish(
            job_id,
            "succeeded",
            result={"type": "url", "source_url": "https://api.toonflow.net/result.mp4"},
            upstream_task_id="cgt-test",
            upstream_status="completed",
        )
        return job_id

    def test_sqlite_lease_prevents_duplicate_claim_and_recovers_after_expiry(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.make_pending(store)

            first = store.due_settlement_jobs({"toonflow"}, lease_seconds=15)
            second = store.due_settlement_jobs({"toonflow"}, lease_seconds=15)

            self.assertEqual([row["job_id"] for row in first], [job_id])
            self.assertEqual(second, [])
            with store.connect() as connection:
                connection.execute(
                    "update video_jobs set settlement_next_query_at=0,settlement_query_started_at=0 where job_id=?",
                    (job_id,),
                )
            reopened = Store(pathlib.Path(directory))
            recovered = reopened.due_settlement_jobs({"toonflow"}, lease_seconds=15)
            self.assertEqual([row["job_id"] for row in recovered], [job_id])
            self.assertEqual(int(recovered[0]["settlement_query_attempts"]), 2)

    def test_gateway_applies_one_idempotent_actual_cost_settlement(self):
        class Collector:
            def collect(self, task_id):
                return BillingRecord(
                    provider_task_id=task_id,
                    actual_cost_status="actual",
                    actual_cost_cny_exact="0.513515",
                    evidence_source="toonflow_web_operation_log",
                    evidence_id="toonflow-operation-log:test",
                    observed_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = Gateway.__new__(Gateway)
            gateway.store = Store(pathlib.Path(directory))
            gateway.billing_collectors = {"toonflow": Collector()}
            gateway.settlement_slots = threading.BoundedSemaphore(2)
            gateway.config = SimpleNamespace(settlement_query_interval_seconds=60)
            job_id = self.make_pending(gateway.store)
            job = gateway.store.due_settlement_jobs({"toonflow"})[0]

            gateway._collect_settlement_one(job)
            gateway._collect_settlement_one(job)

            settled = gateway.store.get(job_id=job_id, internal=True)
            self.assertEqual(settled["billing_status"], "settled")
            self.assertEqual(settled["charged_cny_exact"], "0.770273")
            self.assertEqual(settled["refund_cny_exact"], "5.191327")
            self.assertEqual(settled["supplement_cny_exact"], "0.000000")
            self.assertEqual(settled["settlement_query_last_error"], "")
            with gateway.store.connect() as connection:
                settlements = connection.execute(
                    "select count(*) from video_settlements where job_id=?", (job_id,)
                ).fetchone()[0]
                events = connection.execute(
                    "select count(*) from video_webhook_outbox where job_id=? and event_type='video.billing.settled'",
                    (job_id,),
                ).fetchone()[0]
            self.assertEqual(settlements, 1)
            self.assertEqual(events, 1)

    def test_auth_failure_keeps_result_frozen_and_records_only_safe_code(self):
        class Collector:
            def collect(self, _task_id):
                raise BillingCollectionError(
                    "provider_billing_authentication_failed", retry_after_seconds=3600
                )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = Gateway.__new__(Gateway)
            gateway.store = Store(pathlib.Path(directory))
            gateway.billing_collectors = {"toonflow": Collector()}
            gateway.settlement_slots = threading.BoundedSemaphore(1)
            gateway.config = SimpleNamespace(settlement_query_interval_seconds=60)
            job_id = self.make_pending(gateway.store)
            job = gateway.store.due_settlement_jobs({"toonflow"})[0]

            gateway._collect_settlement_one(job)

            pending = gateway.store.get(job_id=job_id, internal=True)
            self.assertEqual(pending["billing_status"], "settlement_pending")
            self.assertEqual(pending["settlement_query_last_error"], "provider_billing_authentication_failed")
            self.assertEqual(gateway.store.get(job_id=job_id)["result_delivery"], "pending_settlement")
            with gateway.store.connect() as connection:
                self.assertEqual(connection.execute("select count(*) from video_settlements").fetchone()[0], 0)

    def test_automatic_settlement_preserves_less_equal_and_supplement_math(self):
        class Collector:
            def __init__(self, amount):
                self.amount = amount

            def collect(self, task_id):
                return BillingRecord(
                    provider_task_id=task_id,
                    actual_cost_status="actual",
                    actual_cost_cny_exact=self.amount,
                    evidence_source="toonflow_web_operation_log",
                    evidence_id="toonflow-operation-log:" + self.amount,
                    observed_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                )

        cases = (
            ("1.450000", "2.175000", "3.786600", "0.000000"),
            ("3.974400", "5.961600", "0.000000", "0.000000"),
            ("5.009400", "7.514100", "0.000000", "1.552500"),
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            for index, (actual, charged, refund, supplement) in enumerate(cases):
                with self.subTest(actual=actual):
                    gateway = Gateway.__new__(Gateway)
                    gateway.store = store
                    gateway.billing_collectors = {"toonflow": Collector(actual)}
                    gateway.settlement_slots = threading.BoundedSemaphore(1)
                    gateway.config = SimpleNamespace(settlement_query_interval_seconds=60)
                    job_id = self.make_pending(store, request_id=f"math-{index}")
                    job = store.due_settlement_jobs({"toonflow"})[0]
                    gateway._collect_settlement_one(job)
                    settled = store.get(job_id=job_id, internal=True)
                    self.assertEqual(settled["charged_cny_exact"], charged)
                    self.assertEqual(settled["refund_cny_exact"], refund)
                    self.assertEqual(settled["supplement_cny_exact"], supplement)

    def test_disabled_collector_claims_no_pending_jobs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.make_pending(store)
            self.assertEqual(store.due_settlement_jobs(set()), [])
            pending = store.get(job_id=job_id, internal=True)
            self.assertEqual(int(pending["settlement_query_attempts"]), 0)
            self.assertEqual(pending["billing_status"], "settlement_pending")


if __name__ == "__main__":
    unittest.main()
