import pathlib
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import Observation, ProviderConfig
from app import Gateway
from billing_collectors import BillingRecord, NewAPITaskBillingCollector, ToonflowBillingCollector
from store import Store


class RunningAdapter:
    def __init__(self, provider_id="toonflow"):
        self.config = ProviderConfig(provider_id, "https://example.com", "secret", ("example.com",))
        self.calls = []

    def submit(self, request_id, upstream_model, payload):
        self.calls.append(request_id)
        return Observation(status="running", upstream_task_id="next-task", upstream_status="queued")


def create_two_route_job(store):
    snapshot, _ = store.create(
        request_id="recover-request",
        fingerprint="recover-fingerprint",
        protocol_version="xtai-relay-v1",
        catalog_revision="test",
        stable_model="seedance-2.0",
        provider_id="paisio",
        upstream_model="sd3-720p",
        adapter_revision="paisio-v1",
        payload_json='{"_route":{"resolution":"720p"}}',
        route_plan=[
            {"provider_id":"paisio","upstream_model":"sd3-720p","adapter_revision":"paisio-v1"},
            {"provider_id":"toonflow","upstream_model":"Seedance 2.0","adapter_revision":"toonflow-v1"},
        ],
    )
    return str(snapshot["job_id"])


class VideoAutoRecoveryTests(unittest.TestCase):
    def test_failed_zero_cost_attempt_advances_once_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = create_two_route_job(store)
            store.claim_submit(job_id)
            store.begin_recovery(
                job_id,
                error={"code":"failed"},
                upstream_task_id="paisio-execution",
                upstream_status="failed",
                delay_seconds=1,
            )
            advanced = store.complete_failed_attempt(
                job_id,
                provider_task_id="task_billing",
                actual_cost_cny_exact="0.000000",
                evidence_source="paisio_authenticated_failed_request_ledger",
                evidence_id="failed-evidence-1",
                observed_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                error={"code":"failed"},
            )
            self.assertTrue(advanced)
            job = store.get(job_id=job_id, internal=True)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["provider_id"], "toonflow")
            with store.connect() as connection:
                row = connection.execute("select * from video_job_attempts where job_id=? and route_index=0", (job_id,)).fetchone()
            self.assertEqual(row["state"], "failed")
            self.assertEqual(row["actual_cost_cny_exact"], "0.000000")

    def test_uncertain_submit_reuses_exact_idempotency_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = create_two_route_job(store)
            first = store.claim_submit(job_id)
            key = first["_submission_request_id"]
            store.begin_recovery(job_id, error={"code":"timeout","uncertain":True}, delay_seconds=1)
            self.assertTrue(store.retry_uncertain_submit(job_id))
            second = store.claim_submit(job_id)
            self.assertEqual(second["_submission_request_id"], key)
            self.assertEqual(second["provider_id"], "paisio")

    def test_gateway_recovery_advances_after_authoritative_failed_evidence(self):
        class Collector:
            ready = True
            identity_ready = True

            def collect_failed(self, task_id):
                return BillingRecord(
                    provider_task_id="task_billing",
                    execution_task_id=task_id,
                    actual_cost_status="zero_verified",
                    actual_cost_cny_exact="0.000000",
                    evidence_source="paisio_authenticated_failed_request_ledger",
                    evidence_id="failed-evidence-gateway",
                    observed_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = Gateway.__new__(Gateway)
            gateway.store = Store(pathlib.Path(directory))
            job_id = create_two_route_job(gateway.store)
            gateway.store.claim_submit(job_id)
            gateway.store.begin_recovery(job_id, error={"code":"failed"}, upstream_task_id="paisio-execution", delay_seconds=1)
            gateway.billing_collectors = {"paisio": Collector()}
            gateway.settlement_slots = threading.BoundedSemaphore(1)
            gateway.start_submit = lambda value: None
            gateway._recover_one(gateway.store.get(job_id=job_id, internal=True))
            job = gateway.store.get(job_id=job_id, internal=True)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["provider_id"], "toonflow")

    def test_last_failed_attempt_settles_aggregate_cost_once(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            snapshot, _ = store.create(
                request_id="terminal-failed-request",
                fingerprint="terminal-failed-fingerprint",
                protocol_version="xtai-relay-v1",
                catalog_revision="test",
                stable_model="seedance-2.0",
                provider_id="toonflow",
                upstream_model="Seedance 2.0",
                adapter_revision="toonflow-v1",
                payload_json=(
                    '{"duration":4,"_billing_v2":true,"_billing_contract_version":"xtai-video-billing-v2.1",'
                    '"_relay_price":{"contract_version":"xtai-video-pricing-v1","currency":"CNY",'
                    '"amount_cny_exact":"5.000000","official_cost_cny_exact":"3.333333",'
                    '"fallback_multiplier_exact":"1.5","pricing_revision":"test",'
                    '"price_source":"ark_official_1_5"}}'
                ),
                route_plan=[{"provider_id":"toonflow","upstream_model":"Seedance 2.0","adapter_revision":"toonflow-v1"}],
            )
            job_id = str(snapshot["job_id"])
            store.claim_submit(job_id)
            store.begin_recovery(job_id, error={"code":"failed"}, upstream_task_id="cgt-failed", delay_seconds=1)
            observed = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
            advanced = store.complete_failed_attempt(
                job_id,
                provider_task_id="cgt-failed",
                actual_cost_cny_exact="1.250001",
                evidence_source="toonflow_web_failed_operation_log",
                evidence_id="terminal-failed-evidence",
                observed_at=observed,
                error={"code":"failed"},
            )
            self.assertFalse(advanced)
            job = store.get(job_id=job_id, internal=True)
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["billing_status"], "settled")
            self.assertEqual(job["charged_cny_exact"], "1.875002")
            self.assertEqual(job["refund_cny_exact"], "3.124998")
            with store.connect() as connection:
                self.assertEqual(connection.execute("select count(*) from video_settlements where job_id=?", (job_id,)).fetchone()[0], 1)
                failed_event = connection.execute(
                    "select payload_json from video_webhook_outbox where job_id=? and event_type='video.task.failed'",
                    (job_id,),
                ).fetchone()
                settled_event = connection.execute(
                    "select payload_json from video_webhook_outbox where job_id=? and event_type='video.billing.settled'",
                    (job_id,),
                ).fetchone()
            import json
            self.assertEqual(json.loads(failed_event[0])["data"]["billing"]["status"], "settlement_pending")
            self.assertEqual(json.loads(settled_event[0])["data"]["billing"]["status"], "settled")

    def test_decimal_attempt_sum_never_uses_binary_float(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = create_two_route_job(store)
            store.claim_submit(job_id)
            store.begin_recovery(job_id, error={"code":"failed"}, upstream_task_id="task-a", delay_seconds=1)
            store.complete_failed_attempt(
                job_id,
                provider_task_id="bill-a",
                actual_cost_cny_exact="0.100001",
                evidence_source="test",
                evidence_id="decimal-a",
                observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                error={"code":"failed"},
            )
            self.assertEqual(store.attempt_cost_total(job_id), "0.100001")


if __name__ == "__main__":
    unittest.main()
