import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import Gateway
from billing_collectors import BillingCollectionError, BillingRecord, NewAPITaskBillingCollector
from store import BILLING_CONTRACT_VERSION, Store, StoreConflict, build_settlement_evidence


MEDIA = b"\x00\x00\x00\x18ftypmp42" + b"verified-video" * 32


class StreamResponse:
    status = 200

    def __init__(self, payload, *, status=200, declared=None):
        self.payload = payload
        self.status = status
        self.offset = 0
        self.headers = {
            "Content-Length": str(len(payload) if declared is None else declared),
            "Content-Type": "video/mp4",
        }

    def read(self, limit):
        chunk = self.payload[self.offset : self.offset + limit]
        self.offset += len(chunk)
        return chunk

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


class JsonOpener:
    def __init__(self, pages, ledger_factory=None):
        self.pages = pages
        self.ledger_factory = ledger_factory or ledger_payload
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        parsed = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("/api/log/self"):
            task_id = str((query.get("request_id") or [""])[0])
            payload = self.ledger_factory(task_id)
        elif query.get("task_id"):
            task_id = str(query["task_id"][0])
            rows = [
                row
                for page in self.pages.values()
                for row in page
                if str(row.get("task_id") or "") == task_id
            ]
            payload = {"success": True, "data": {"total": len(rows), "items": rows}}
        else:
            page = int((query.get("p") or [1])[0])
            total = sum(len(rows) for rows in self.pages.values())
            payload = {
                "success": True,
                "data": {
                    "page": page,
                    "page_size": 100,
                    "total": total,
                    "items": self.pages.get(page, []),
                },
            }
        return StreamResponse(json.dumps(payload, separators=(",", ":")).encode())


class MediaOpener:
    def __init__(self, values):
        self.values = values
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        value = self.values[request.full_url]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, tuple):
            payload, declared = value
            return StreamResponse(payload, declared=declared)
        return StreamResponse(value)


def ledger_payload(task_id):
    return {
        "success": True,
        "data": {
            "total": 2,
            "items": [
                {
                    "id": 1,
                    "type": 2,
                    "request_id": task_id,
                    "quota": 500000,
                    "other": '{"billing_type":"per_call"}',
                },
                {
                    "id": 2,
                    "type": 2,
                    "request_id": task_id,
                    "quota": 0,
                    "other": '{"billing_type":"completed"}',
                },
            ],
        },
    }


def public_resolver(host, port, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def task_row(task_id="task_identity_alpha", **overrides):
    now = int(time.time())
    row = {
        "id": 101,
        "task_id": task_id,
        "action": "videoGenerate",
        "status": "SUCCESS",
        "submit_time": now - 200,
        "finish_time": now - 100,
        "data": {"video_url": f"https://cdn.paisio.online/{task_id}.mp4"},
    }
    row.update(overrides)
    return row


def job_for(row, **overrides):
    job = {
        "job_id": "vjob_" + "a" * 32,
        "provider_id": "paisio",
        "status": "succeeded",
        "billing_status": "settlement_pending",
        "upstream_task_id": "execution-identity-alpha",
        "submit_started_at": int(row["submit_time"]) - 1,
        "submit_confirmed_at": int(row["submit_time"]) + 1,
        "finished_at": int(row["finish_time"]),
        "created_at": int(row["submit_time"]) - 1,
        "result_json": json.dumps(
            {
                "type": "url",
                "source_url": "https://cdn.paisio.online/gateway-result.mp4",
                "requires_auth": False,
            }
        ),
    }
    job.update(overrides)
    return job


def collector_for(pages, media=None, **kwargs):
    return NewAPITaskBillingCollector(
        "paisio",
        "https://api.paisio.online/api/task/self",
        authorization="Bearer read-only-session",
        new_api_user="42",
        result_hosts=("cdn.paisio.online",),
        opener=JsonOpener(pages),
        media_opener=MediaOpener(
            media
            or {
                "https://cdn.paisio.online/gateway-result.mp4": MEDIA,
                **{
                    str(row["data"]["video_url"]): MEDIA
                    for rows in pages.values()
                    for row in rows
                    if isinstance(row.get("data"), dict) and row["data"].get("video_url")
                },
            }
        ),
        host_resolver=public_resolver,
        **kwargs,
    )


class PaisioIdentityResolverTests(unittest.TestCase):
    def assert_code(self, code, collector, job, **kwargs):
        with self.assertRaises(BillingCollectionError) as failure:
            collector.resolve_and_collect(job, **kwargs)
        self.assertEqual(failure.exception.code, code)

    def test_unique_terminal_time_and_media_match_resolves_dual_identity(self):
        row = task_row()
        record = collector_for({1: [row]}).resolve_and_collect(job_for(row))
        self.assertEqual(record.provider_task_id, row["task_id"])
        self.assertEqual(record.execution_task_id, "execution-identity-alpha")
        self.assertEqual(record.actual_cost_cny_exact, "1.000000")
        self.assertEqual(record.evidence_source, "paisio_authenticated_request_ledger")
        self.assertEqual(record.media_size_bytes, len(MEDIA))
        self.assertEqual(len(record.media_sha256), 64)

    def test_noise_rows_are_filtered_before_media_comparison(self):
        row = task_row()
        noise = [
            task_row("task_noise_action", action="imageGenerate"),
            task_row("task_noise_failure", status="FAILURE"),
            task_row("task_noise_time", submit_time=row["submit_time"] - 1000),
        ]
        record = collector_for({1: noise + [row]}).resolve_and_collect(job_for(row))
        self.assertEqual(record.provider_task_id, row["task_id"])

    def test_page_three_candidate_is_found_with_complete_pagination(self):
        row = task_row()
        noise = [
            task_row(f"task_noise_{index:04d}", submit_time=row["submit_time"] - 1000 - index)
            for index in range(200)
        ]
        record = collector_for({1: noise[:100], 2: noise[100:], 3: [row]}).resolve_and_collect(
            job_for(row)
        )
        self.assertEqual(record.provider_task_id, row["task_id"])

    def test_no_candidate_fails_closed(self):
        row = task_row()
        other = task_row("task_too_old", submit_time=row["submit_time"] - 1000)
        self.assert_code(
            "provider_billing_record_not_ready", collector_for({1: [other]}), job_for(row)
        )

    def test_two_matching_candidates_are_ambiguous(self):
        first = task_row()
        second = task_row("task_identity_beta", id=102)
        self.assert_code(
            "provider_billing_record_ambiguous",
            collector_for({1: [first, second]}),
            job_for(first),
        )

    def test_wrong_media_hash_fails_closed(self):
        row = task_row()
        media = {
            "https://cdn.paisio.online/gateway-result.mp4": MEDIA,
            row["data"]["video_url"]: MEDIA + b"different",
        }
        self.assert_code(
            "provider_billing_identity_media_mismatch",
            collector_for({1: [row]}, media),
            job_for(row),
        )

    def test_unsafe_media_host_is_rejected(self):
        row = task_row(data={"video_url": "https://evil.example/result.mp4"})
        media = {"https://cdn.paisio.online/gateway-result.mp4": MEDIA}
        self.assert_code(
            "provider_billing_identity_result_host_unsafe",
            collector_for({1: [row]}, media),
            job_for(row),
        )

    def test_credentialed_media_url_is_rejected(self):
        row = task_row(data={"video_url": "https://user:pass@cdn.paisio.online/result.mp4"})
        media = {"https://cdn.paisio.online/gateway-result.mp4": MEDIA}
        self.assert_code(
            "provider_billing_identity_result_url_unsafe",
            collector_for({1: [row]}, media),
            job_for(row),
        )

    def test_wrong_submit_window_is_not_a_candidate(self):
        row = task_row()
        job = job_for(row, submit_started_at=row["submit_time"] + 20, submit_confirmed_at=row["submit_time"] + 21)
        self.assert_code("provider_billing_record_not_ready", collector_for({1: [row]}), job)

    def test_wrong_finish_window_is_not_a_candidate(self):
        row = task_row(finish_time=int(time.time()) - 500)
        job = job_for(row, finished_at=int(time.time()) - 100)
        self.assert_code("provider_billing_record_not_ready", collector_for({1: [row]}), job)

    def test_unscanned_page_limit_fails_before_guessing(self):
        row = task_row()
        pages = {
            1: [row] + [task_row(f"task_noise_{i:04d}") for i in range(99)],
            2: [task_row("task_noise_last")],
        }
        self.assert_code(
            "provider_billing_identity_page_limit",
            collector_for(pages, identity_max_pages=1),
            job_for(row),
        )

    def test_oversize_media_is_rejected(self):
        row = task_row()
        media = {
            "https://cdn.paisio.online/gateway-result.mp4": (MEDIA, len(MEDIA) + 10_000),
            row["data"]["video_url"]: MEDIA,
        }
        self.assert_code(
            "provider_billing_identity_media_too_large",
            collector_for({1: [row]}, media, max_media_bytes=len(MEDIA) + 100),
            job_for(row),
        )

    def test_media_redirect_is_rejected(self):
        row = task_row()
        redirect = urllib.error.HTTPError(
            row["data"]["video_url"], 302, "redirect", {"Location": row["data"]["video_url"]}, None
        )
        media = {
            "https://cdn.paisio.online/gateway-result.mp4": MEDIA,
            row["data"]["video_url"]: redirect,
        }
        self.assert_code(
            "provider_billing_identity_media_redirect",
            collector_for({1: [row]}, media),
            job_for(row),
        )

    def test_changed_pagination_snapshot_fails_closed(self):
        row = task_row()

        class ChangingJsonOpener(JsonOpener):
            def open(self, request, timeout):
                response = super().open(request, timeout)
                parsed = urllib.parse.urlsplit(request.full_url)
                query = urllib.parse.parse_qs(parsed.query)
                if int((query.get("p") or [1])[0]) == 2:
                    payload = json.loads(response.payload)
                    payload["data"]["total"] += 1
                    return StreamResponse(json.dumps(payload).encode())
                return response

        noise = [task_row(f"task_noise_{i:04d}") for i in range(100)]
        collector = collector_for({1: noise, 2: [row]})
        collector._opener = ChangingJsonOpener({1: noise, 2: [row]})
        self.assert_code("provider_billing_identity_snapshot_changed", collector, job_for(row))

    def test_missing_submit_window_requires_explicit_historical_mode(self):
        row = task_row()
        job = job_for(row, submit_started_at=0, submit_confirmed_at=0, created_at=row["submit_time"] - 1)
        self.assert_code("provider_billing_identity_window_missing", collector_for({1: [row]}), job)

    def test_explicit_historical_mode_still_requires_media_identity(self):
        row = task_row()
        job = job_for(row, submit_started_at=0, submit_confirmed_at=0, created_at=row["submit_time"] - 1)
        record = collector_for({1: [row]}).resolve_and_collect(job, allow_historical=True)
        self.assertEqual(record.provider_task_id, row["task_id"])

    def test_private_dns_resolution_is_rejected(self):
        row = task_row()
        collector = collector_for({1: [row]})
        collector._host_resolver = lambda host, port, **kwargs: [(2, 1, 6, "", ("127.0.0.1", port))]
        self.assert_code(
            "provider_billing_identity_result_host_unsafe", collector, job_for(row)
        )


def priced_payload():
    return json.dumps(
        {
            "model": "seedance-2.0",
            "duration": 4,
            "_billing_v2": True,
            "_billing_contract_version": BILLING_CONTRACT_VERSION,
            "_relay_price": {
                "contract_version": "xtai-video-pricing-v1",
                "currency": "CNY",
                "amount_cny_exact": "5.961600",
                "official_cost_cny_exact": "3.974400",
                "fallback_multiplier_exact": "1.5",
                "pricing_revision": "identity-test",
                "price_source": "ark_official_1_5",
            },
        },
        separators=(",", ":"),
    )


class ProviderTaskBindingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(pathlib.Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def succeeded_job(self, request_id, execution_id):
        snapshot, _ = self.store.create(
            request_id=request_id,
            fingerprint=request_id.rjust(64, "0")[-64:],
            protocol_version="xtai-video-relay-v1",
            catalog_revision="test",
            stable_model="seedance-2.0",
            provider_id="paisio",
            upstream_model="raw-model",
            adapter_revision="test",
            payload_json=priced_payload(),
        )
        job_id = snapshot["job_id"]
        self.store.claim_submit(job_id)
        self.store.mark_running(job_id, execution_id, "submitted", 1)
        self.store.finish(
            job_id,
            "succeeded",
            result={"source_url": "https://cdn.paisio.online/result.mp4"},
            upstream_task_id=execution_id,
            upstream_status="succeeded",
        )
        return job_id

    def binding_args(self, job_id, execution_id, billing_id="task_binding_alpha"):
        now = int(time.time())
        return {
            "job_id": job_id,
            "provider_id": "paisio",
            "execution_task_id": execution_id,
            "billing_task_id": billing_id,
            "resolver_version": "paisio-dual-task-id-v1",
            "provider_record_id": "101",
            "provider_submit_time": now - 100,
            "provider_finish_time": now - 10,
            "media_size_bytes": len(MEDIA),
            "media_sha256": __import__("hashlib").sha256(MEDIA).hexdigest(),
        }

    def test_submit_window_is_persisted_around_execution_confirmation(self):
        job_id = self.succeeded_job("request-window", "execution-window")
        job = self.store.get(job_id=job_id, internal=True)
        self.assertGreater(int(job["submit_started_at"]), 0)
        self.assertGreaterEqual(int(job["submit_confirmed_at"]), int(job["submit_started_at"]))

    def test_binding_is_idempotent_but_same_job_conflict_is_rejected(self):
        job_id = self.succeeded_job("request-bind", "execution-bind")
        args = self.binding_args(job_id, "execution-bind")
        _binding, reused = self.store.bind_provider_task(**args)
        self.assertFalse(reused)
        _binding, reused = self.store.bind_provider_task(**args)
        self.assertTrue(reused)
        with self.assertRaises(StoreConflict):
            self.store.bind_provider_task(**{**args, "billing_task_id": "task_binding_other"})

    def test_cross_job_billing_identity_reuse_is_rejected(self):
        first = self.succeeded_job("request-first", "execution-first")
        second = self.succeeded_job("request-second", "execution-second")
        self.store.bind_provider_task(**self.binding_args(first, "execution-first"))
        with self.assertRaises(StoreConflict):
            self.store.bind_provider_task(**self.binding_args(second, "execution-second"))

    def test_concurrent_identical_binding_inserts_once(self):
        job_id = self.succeeded_job("request-concurrent", "execution-concurrent")
        args = self.binding_args(job_id, "execution-concurrent")
        outcomes = []

        def bind():
            outcomes.append(self.store.bind_provider_task(**args)[1])

        threads = [threading.Thread(target=bind), threading.Thread(target=bind)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), [False, True])

    def test_binding_survives_store_restart_without_rewriting_execution_id(self):
        job_id = self.succeeded_job("request-restart", "execution-restart")
        args = self.binding_args(job_id, "execution-restart", "task_binding_restart")
        self.store.bind_provider_task(**args)

        reopened = Store(pathlib.Path(self.temp.name))
        binding = reopened.get_provider_task_binding(job_id)
        job = reopened.get(job_id=job_id, internal=True)
        self.assertEqual(binding["billing_task_id"], "task_binding_restart")
        self.assertEqual(job["upstream_task_id"], "execution-restart")

    def test_settlement_requires_bound_billing_identity_and_preserves_execution_id(self):
        job_id = self.succeeded_job("request-settle", "execution-settle")
        args = self.binding_args(job_id, "execution-settle")
        self.store.bind_provider_task(**args)
        evidence = build_settlement_evidence(
            job_id=job_id,
            revision=1,
            provider_task_id=args["billing_task_id"],
            actual_cost_status="actual",
            actual_cost_cny_exact="1.450000",
            evidence_source="paisio_authenticated_request_ledger",
            evidence_id="paisio-request-ledger:" + "a" * 64,
            observed_at=time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.gmtime(time.time() + 8 * 3600)),
        )
        snapshot, reused = self.store.apply_settlement(evidence)
        self.assertFalse(reused)
        self.assertEqual(snapshot["billing"]["charged_amount"], "2.175000")
        job = self.store.get(job_id=job_id, internal=True)
        self.assertEqual(job["upstream_task_id"], "execution-settle")

    def test_bound_job_rejects_execution_identity_as_settlement_evidence(self):
        job_id = self.succeeded_job("request-mismatch", "execution-mismatch")
        self.store.bind_provider_task(**self.binding_args(job_id, "execution-mismatch"))
        evidence = build_settlement_evidence(
            job_id=job_id,
            revision=1,
            provider_task_id="execution-mismatch",
            actual_cost_status="actual",
            actual_cost_cny_exact="1.000000",
            evidence_source="provider_account_ledger",
            evidence_id="mismatch-evidence",
            observed_at=time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.gmtime(time.time() + 8 * 3600)),
        )
        with self.assertRaises(StoreConflict):
            self.store.apply_settlement(evidence)

    def test_gateway_collector_binds_then_settles_once_and_replay_is_inert(self):
        job_id = self.succeeded_job("request-gateway", "execution-gateway")
        now = int(time.time())

        class Collector:
            resolve_calls = 0
            collect_calls = 0

            def resolve_and_collect(self, job):
                self.resolve_calls += 1
                return BillingRecord(
                    provider_task_id="task_gateway_binding",
                    actual_cost_status="actual",
                    actual_cost_cny_exact="1.450000",
                    evidence_source="paisio_authenticated_request_ledger",
                    evidence_id="paisio-request-ledger:" + "b" * 64,
                    observed_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%S+08:00", time.gmtime(time.time() + 8 * 3600)
                    ),
                    execution_task_id="execution-gateway",
                    resolver_version="paisio-dual-task-id-v1",
                    provider_record_id="202",
                    provider_submit_time=now - 100,
                    provider_finish_time=now - 10,
                    media_size_bytes=len(MEDIA),
                    media_sha256=__import__("hashlib").sha256(MEDIA).hexdigest(),
                )

            def collect(self, task_id):
                self.collect_calls += 1
                raise AssertionError("settled replay must not recollect")

        collector = Collector()
        gateway = SimpleNamespace(
            store=self.store,
            billing_collectors={"paisio": collector},
            settlement_slots=threading.BoundedSemaphore(1),
            config=SimpleNamespace(settlement_query_interval_seconds=60),
        )
        job = self.store.get(job_id=job_id, internal=True)
        Gateway._collect_settlement_one(gateway, job)
        settled = self.store.get(job_id=job_id, internal=True)
        self.assertEqual(settled["billing_status"], "settled")
        self.assertEqual(collector.resolve_calls, 1)
        self.assertEqual(self.store.get_provider_task_binding(job_id)["billing_task_id"], "task_gateway_binding")

        Gateway._collect_settlement_one(gateway, job)
        self.assertEqual(collector.resolve_calls, 1)
        self.assertEqual(collector.collect_calls, 0)


if __name__ == "__main__":
    unittest.main()
