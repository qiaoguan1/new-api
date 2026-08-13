import hashlib
import hmac
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import Observation, ProviderConfig, ToonflowAdapter
from app import Config, Gateway, GatewayError, handler_class
from store import BILLING_CONTRACT_LEGACY, BILLING_CONTRACT_REFERENCE_VERSION, BILLING_CONTRACT_VERSION, Store


class IdleAdapter:
    def __init__(self, provider_id: str):
        self.config = ProviderConfig(
            provider_id,
            "https://example.com",
            "secret",
            ("example.com",),
        )

    @property
    def ready_for_new_jobs(self):
        return True

    def submit(self, request_id, upstream_model, payload):
        return Observation(status="running", upstream_task_id="provider-task-1")


def settlement(job_id: str) -> dict:
    value = {
        "contract_version": BILLING_CONTRACT_VERSION,
        "job_id": job_id,
        "revision": 1,
        "provider_task_id": "provider-task-1",
        "actual_cost_status": "actual",
        "actual_cost_cny_exact": "1.000000",
        "evidence_source": "provider_account_ledger",
        "evidence_id": "ledger-row-1",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    material = "\0".join(
        str(value[name])
        for name in (
            "contract_version",
            "job_id",
            "provider_task_id",
            "actual_cost_status",
            "actual_cost_cny_exact",
            "evidence_source",
            "evidence_id",
            "observed_at",
        )
    )
    value["evidence_fingerprint"] = hashlib.sha256(material.encode()).hexdigest()
    value["settlement_id"] = hashlib.sha256(
        "\0".join(("xtai-video-settlement-v2", job_id, "1", value["evidence_fingerprint"])).encode()
    ).hexdigest()
    return value


class BillingV2ProtocolTests(unittest.TestCase):
    def make_gateway(self, directory: str, *, webhook: bool = False, v22: bool = False) -> Gateway:
        adapters = {name: IdleAdapter(name) for name in ("paisio", "toonflow")}
        config = Config(
            token="test-token",
            data_dir=pathlib.Path(directory),
            catalog_file=ROOT / "catalog.json",
            providers={name: adapter.config for name, adapter in adapters.items()},
            pricing_file=ROOT / "relay-pricing.json",
            public_base_url="https://api.aixingtuyun.com",
            webhook_enabled=webhook,
            webhook_url="https://cloud.aixingtuyun.com/api/webhooks/video" if webhook else "",
            webhook_secret="test-webhook-secret-that-is-at-least-32-bytes" if webhook else "",
            v22_reference_video_enabled=v22,
            v22_reference_audio_enabled=v22,
            v22_reference_combined_enabled=v22,
        )
        gateway = Gateway(
            config,
            adapters=adapters,
            billing_collectors={name: SimpleNamespace(ready=True) for name in adapters},
            reference_verifier=SimpleNamespace(
                verify=lambda _references: None,
                verify_image_origins=lambda _urls: None,
            ),
            start_monitor=False,
        )
        gateway.start_submit = lambda _job_id: None
        return gateway

    @staticmethod
    def body(request_id="billing-v2-request"):
        return {
            "provider_id": "video-aixingtu-api",
            "request_id": request_id,
            "model": "seedance-2.0",
            "resolution": "720p",
            "duration": 4,
            "aspect_ratio": "16:9",
            "generate_audio": False,
            "prompt": "test motion",
        }

    def test_v2_freezes_official_quote_and_replays_one_job(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            first, reused = gateway.submit_v2(self.body(), idempotency_key="billing-v2-request")
            replay, replayed = gateway.submit_v2(self.body(), idempotency_key="billing-v2-request")
            self.assertFalse(reused)
            self.assertTrue(replayed)
            self.assertEqual(first["job_id"], replay["job_id"])
            self.assertEqual(first["billing"]["status"], "reserved")
            self.assertEqual(first["billing"]["reserved_amount"], "5.961600")
            internal = gateway.store.get(job_id=first["job_id"], internal=True)
            self.assertIn('"_billing_v2":true', internal["payload_json"])
            self.assertIn(f'"_billing_contract_version":"{BILLING_CONTRACT_VERSION}"', internal["payload_json"])
            self.assertIn('"generate_audio":false', internal["payload_json"])

    def test_v21_all_seven_generated_audio_variants_use_audio_capable_route(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            variants = (
                ("seedance-2.0", "480p"),
                ("seedance-2.0", "720p"),
                ("seedance-2.0", "1080p"),
                ("seedance-2.0-fast", "480p"),
                ("seedance-2.0-fast", "720p"),
                ("seedance-2.0-mini", "480p"),
                ("seedance-2.0-mini", "720p"),
            )
            for index, (model, resolution) in enumerate(variants):
                with self.subTest(model=model, resolution=resolution):
                    request_id = f"billing-v21-audio-{index}"
                    value = self.body(request_id)
                    value.update(
                        model=model,
                        resolution=resolution,
                        generate_audio=True,
                    )
                    created, reused = gateway.submit_v2(value, idempotency_key=request_id)

                    self.assertFalse(reused)
                    internal = gateway.store.get(job_id=created["job_id"], internal=True)
                    self.assertEqual(internal["provider_id"], "toonflow")
                    payload = json.loads(internal["payload_json"])
                    self.assertIs(payload["generate_audio"], True)
                    request_body = ToonflowAdapter.request_body(
                        gateway.adapters["toonflow"], internal["upstream_model"], payload
                    )
                    self.assertIs(request_body["metadata"]["generate_audio"], True)

    def test_v21_requires_explicit_boolean_generated_audio(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            missing = self.body("billing-v21-audio-missing")
            del missing["generate_audio"]
            with self.assertRaisesRegex(GatewayError, "generate_audio"):
                gateway.submit_v2(missing, idempotency_key="billing-v21-audio-missing")

            invalid = self.body("billing-v21-audio-invalid")
            invalid["generate_audio"] = "true"
            with self.assertRaisesRegex(GatewayError, "generate_audio"):
                gateway.submit_v2(invalid, idempotency_key="billing-v21-audio-invalid")

    def test_v2_image_identity_keeps_idempotency_stable_when_signed_url_rotates(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            identity = hashlib.sha256(b"same-reference-image").hexdigest()
            first_body = self.body("billing-v2-image-request")
            first_body["image"] = "https://tos.example.com/input/reference.png?signature=first"
            first_body["image_identities"] = [identity]
            first, reused = gateway.submit_v2(first_body, idempotency_key="billing-v2-image-request")

            rotated_body = self.body("billing-v2-image-request")
            rotated_body["image"] = "https://tos.example.com/input/reference.png?signature=rotated"
            rotated_body["image_identities"] = [identity]
            replay, replayed = gateway.submit_v2(rotated_body, idempotency_key="billing-v2-image-request")

            self.assertFalse(reused)
            self.assertTrue(replayed)
            self.assertEqual(first["job_id"], replay["job_id"])

            changed_body = dict(rotated_body)
            changed_body["image_identities"] = [hashlib.sha256(b"different-reference-image").hexdigest()]
            with self.assertRaises(GatewayError):
                gateway.submit_v2(changed_body, idempotency_key="billing-v2-image-request")

    def test_v2_rejects_invalid_image_identity(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            value = self.body("billing-v2-invalid-image-identity")
            value["image"] = "https://tos.example.com/input/reference.png"
            value["image_identities"] = ["not-a-sha256"]
            with self.assertRaises(GatewayError):
                gateway.submit_v2(value, idempotency_key="billing-v2-invalid-image-identity")

    def test_v21_rejects_all_reference_video_and_audio_field_variants(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            for field in ("video", "videos", "audio", "audios", "reference_videos", "reference_audios"):
                request_id = f"billing-v21-reject-{field}"
                value = self.body(request_id)
                value[field] = ["https://media.example.com/reference.mp3"] if field.endswith("s") else "https://media.example.com/reference.mp3"
                with self.subTest(field=field), self.assertRaises(GatewayError) as failure:
                    gateway.submit_v2(value, idempotency_key=request_id)
                self.assertEqual(failure.exception.code, "video_reference_unsupported")

    def test_v22_is_default_closed_before_job_or_freeze(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            request_id = "billing-v22-disabled"
            value = self.body(request_id)
            value["image"] = "https://tos.example.com/frame.png"
            value["image_identities"] = ["b" * 64]
            value["reference_audios"] = [{
                "role": "reference_audio",
                "url": "https://tos.example.com/reference.mp3?temporary-signature",
                "sha256": "a" * 64,
                "mime_type": "audio/mpeg",
                "codec": "mp3",
                "size_bytes": 1024,
                "duration_seconds": "4.000000",
                "sample_rate_hz": 44100,
                "channels": 2,
            }]
            with self.assertRaises(GatewayError) as failure:
                gateway.submit_v22(value, idempotency_key=request_id)
            self.assertEqual(failure.exception.code, "reference_audio_contract_unavailable")
            with gateway.store.connect() as connection:
                self.assertEqual(connection.execute("select count(*) from video_jobs").fetchone()[0], 0)

    def test_v22_audio_with_image_creates_one_durable_toonflow_job(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory, v22=True)
            request_id = "billing-v22-image-audio"
            value = self.body(request_id)
            value["image"] = "https://tos.example.com/frame.png?signature=one"
            value["image_identities"] = ["c" * 64]
            value["reference_audios"] = [{
                "role": "reference_audio",
                "url": "https://tos.example.com/reference.mp3?signature=one",
                "sha256": "d" * 64,
                "mime_type": "audio/mpeg",
                "codec": "mp3",
                "size_bytes": 1024,
                "duration_seconds": "4.000000",
                "sample_rate_hz": 44100,
                "channels": 2,
            }]

            created, reused = gateway.submit_v22(value, idempotency_key=request_id)

            self.assertFalse(reused)
            self.assertEqual(created["billing"]["contract_version"], BILLING_CONTRACT_REFERENCE_VERSION)
            self.assertEqual(created["billing"]["status"], "reserved")
            self.assertEqual(created["billing"]["reserved_amount"], "5.961600")
            internal = gateway.store.get(job_id=created["job_id"], internal=True)
            self.assertEqual(internal["provider_id"], "toonflow")
            payload = json.loads(internal["payload_json"])
            self.assertEqual(payload["audios"][0]["identity"], "d" * 64)
            self.assertEqual(payload["_billing_contract_version"], BILLING_CONTRACT_REFERENCE_VERSION)
            self.assertEqual(created["input"]["reference_audio_count"], 1)

    def test_v22_reference_video_uses_ark_video_input_reservation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory, v22=True)
            request_id = "billing-v22-reference-video"
            value = self.body(request_id)
            value["reference_videos"] = [{
                "role": "reference_video",
                "url": "https://tos.example.com/reference.mp4?signature=one",
                "sha256": "e" * 64,
                "mime_type": "video/mp4",
                "size_bytes": 1024,
                "duration_seconds": "4.000000",
                "width_pixels": 720,
                "height_pixels": 1280,
            }]

            created, _ = gateway.submit_v22(value, idempotency_key=request_id)

            self.assertEqual(created["billing"]["reserved_amount"], "3.628800")
            internal = gateway.store.get(job_id=created["job_id"], internal=True)
            payload = json.loads(internal["payload_json"])
            self.assertEqual(payload["_relay_price"]["input_rate_class"], "with_video_input")

    def test_capability_and_price_catalogs_fail_closed_for_v22(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            capabilities = gateway.capabilities()["capabilities"]["video"]
            self.assertEqual(capabilities["reference_contract_version"], BILLING_CONTRACT_REFERENCE_VERSION)
            for model in capabilities["models"]:
                self.assertTrue(model["reference_video"]["supported"])
                self.assertFalse(model["reference_video"]["available"])
                self.assertEqual(model["reference_video"]["max_count"], 3)
                self.assertTrue(model["reference_audio"]["supported"])
                self.assertFalse(model["reference_audio"]["available"])
                self.assertIn("audio/mpeg", model["reference_audio"]["mime_types"])
            profiles = gateway.video_prices()["billing_v22_input_profiles"]
            self.assertEqual(profiles["contract_version"], BILLING_CONTRACT_REFERENCE_VERSION)
            self.assertEqual(profiles["pricing_mode"], "ark_official_input_mode_1_5")
            self.assertTrue(all(profiles[name]["supported"] for name in ("reference_video", "reference_audio", "reference_video_audio")))
            self.assertEqual(profiles["reference_video"]["official_rate_class"], "with_video_input")
            self.assertEqual(profiles["reference_audio"]["official_rate_class"], "without_video_input")

    def test_v22_catalogs_publish_exact_available_resolutions_and_prices_when_enabled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory, v22=True)
            capabilities = gateway.capabilities()["capabilities"]["video"]
            for model in capabilities["models"]:
                self.assertTrue(model["reference_video"]["available"])
                self.assertTrue(model["reference_audio"]["available"])
                self.assertTrue(model["reference_video_audio"]["available"])
                self.assertEqual(
                    model["reference_audio"]["available_resolutions"],
                    model["resolutions"],
                )
                self.assertTrue(model["reference_audio"]["requires_non_audio_input"])
            rows = gateway.video_prices()["billing_v22_input_profiles"]["models"]
            standard_720 = next(
                row for row in rows
                if row["model"] == "seedance-2.0" and row["resolution"] == "720p"
            )
            self.assertEqual(standard_720["reference_video"]["cny_per_second_exact"], "0.907200")
            self.assertEqual(standard_720["reference_audio"]["cny_per_second_exact"], "1.490400")

    def test_v22_settled_webhook_contains_only_reference_digests_not_urls(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory, webhook=True, v22=True)
            request_id = "billing-v22-webhook"
            value = self.body(request_id)
            value["image"] = "https://tos.example.com/frame.png?secret=image"
            value["image_identities"] = ["1" * 64]
            value["reference_audios"] = [{
                "role": "reference_audio",
                "url": "https://tos.example.com/reference.wav?secret=audio",
                "sha256": "2" * 64,
                "mime_type": "audio/wav",
                "codec": "wav",
                "size_bytes": 1024,
                "duration_seconds": "4.000000",
                "sample_rate_hz": 48000,
                "channels": 2,
            }]
            created, _ = gateway.submit_v22(value, idempotency_key=request_id)
            gateway.store.claim_submit(created["job_id"])
            gateway.store.mark_running(created["job_id"], "provider-task-1", "running", 1)
            gateway.store.finish(created["job_id"], "succeeded", result={"url": "https://result.example.com/video.mp4"})
            evidence = settlement(created["job_id"])
            evidence["contract_version"] = BILLING_CONTRACT_REFERENCE_VERSION
            material = "\0".join(
                str(evidence[name])
                for name in (
                    "contract_version", "job_id", "provider_task_id", "actual_cost_status",
                    "actual_cost_cny_exact", "evidence_source", "evidence_id", "observed_at",
                )
            )
            evidence["evidence_fingerprint"] = hashlib.sha256(material.encode()).hexdigest()
            evidence["settlement_id"] = hashlib.sha256(
                "\0".join(("xtai-video-settlement-v2", created["job_id"], "1", evidence["evidence_fingerprint"])).encode()
            ).hexdigest()
            gateway.store.apply_settlement(evidence)

            events = gateway.store.due_webhook_events(limit=20, lease_seconds=30)
            settled = next(event for event in events if event["event_type"] == "video.billing.settled")
            payload = json.loads(settled["payload_json"])
            self.assertEqual(payload["data"]["input"]["reference_audio_count"], 1)
            self.assertEqual(payload["data"]["result_delivery"], "ready")
            self.assertEqual(
                payload["data"]["result"]["url"],
                f"https://api.aixingtuyun.com/v1/videos/{created['job_id']}/content",
            )
            self.assertNotIn("tos.example.com", settled["payload_json"])
            self.assertNotIn("secret=", settled["payload_json"])

    def test_v2_rejects_mismatched_idempotency_and_singular_undefined_references(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            with self.assertRaisesRegex(GatewayError, "Idempotency-Key"):
                gateway.submit_v2(self.body(), idempotency_key="different-request")
            value = self.body()
            value["video"] = "https://example.com/reference.mp4"
            with self.assertRaises(GatewayError):
                gateway.submit_v2(value, idempotency_key="billing-v2-request")

    def test_v2_result_is_hidden_until_exact_settlement(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            created, _ = gateway.submit_v2(self.body(), idempotency_key="billing-v2-request")
            job_id = created["job_id"]
            self.assertIsNotNone(gateway.store.claim_submit(job_id))
            gateway.store.mark_running(job_id, "provider-task-1", "queued", 5)
            gateway.store.finish(
                job_id,
                "succeeded",
                result={"type": "url", "source_url": "https://example.com/result.mp4"},
                upstream_task_id="provider-task-1",
                upstream_status="completed",
            )
            pending = gateway.public_v2_snapshot(gateway.store.get(job_id=job_id))
            self.assertEqual(pending["result_delivery"], "pending_settlement")
            self.assertIsNone(pending["result"])
            self.assertIsNone(pending["result_url"])
            gateway.apply_settlement(settlement(job_id))
            ready = gateway.public_v2_snapshot(gateway.store.get(job_id=job_id))
            self.assertEqual(ready["billing"]["charged_amount"], "1.500000")
            self.assertEqual(ready["result_delivery"], "ready")
            self.assertEqual(
                ready["result"],
                {"type": "url", "url": f"https://api.aixingtuyun.com/v1/videos/{job_id}/content"},
            )
            self.assertEqual(ready["result_url"], f"https://api.aixingtuyun.com/v1/videos/{job_id}/content")

    def test_legacy_submission_keeps_legacy_delivery_and_pricing_path(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            snapshot, _ = gateway.submit(
                {
                    "protocol_version": "xtai-relay-v1",
                    "request_id": "legacy-request",
                    "model": "seedance-2.0",
                    "input": {"prompt": "legacy"},
                    "parameters": {
                        "resolution": "720p",
                        "duration": 4,
                        "aspect_ratio": "16:9",
                        "mode": "text",
                    },
                }
            )
            self.assertEqual(snapshot["billing"]["status"], "unavailable")
            self.assertEqual(snapshot["billing"]["contract_version"], "")
            with gateway.store.connect() as connection:
                connection.execute(
                    "update video_jobs set billing_contract_version='xtai-video-billing-v2' where job_id=?",
                    (snapshot["job_id"],),
                )
            reopened = Store(pathlib.Path(directory))
            self.assertEqual(reopened.get(job_id=snapshot["job_id"])["billing"]["contract_version"], "")

            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class(gateway))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"http://127.0.0.1:{server.server_port}/v1/videos/{snapshot['job_id']}",
                            headers={
                                "Authorization": "Bearer test-token",
                                "X-XingTu-Contract-Version": BILLING_CONTRACT_LEGACY,
                            },
                        ),
                        timeout=5,
                    )
                self.assertEqual(failure.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_legacy_submission_still_rejects_generated_audio(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            with self.assertRaises(GatewayError) as failure:
                gateway.submit(
                    {
                        "protocol_version": "xtai-relay-v1",
                        "request_id": "legacy-audio-request",
                        "model": "seedance-2.0",
                        "input": {"prompt": "legacy audio"},
                        "parameters": {
                            "resolution": "720p",
                            "duration": 4,
                            "aspect_ratio": "16:9",
                            "mode": "text",
                            "generate_audio": True,
                        },
                    }
                )
            self.assertEqual(failure.exception.code, "video_generate_audio_not_enabled")
            self.assertEqual(gateway.store.active_count(), 0)

    def test_webhook_outbox_is_durable_and_payload_is_stable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory, webhook=True)
            created, _ = gateway.submit_v2(self.body(), idempotency_key="billing-v2-request")
            job_id = created["job_id"]
            self.assertIsNotNone(gateway.store.claim_submit(job_id))
            gateway.store.mark_running(job_id, "provider-task-1", "queued", 5)
            gateway.store.finish(
                job_id,
                "succeeded",
                result={"type": "url", "source_url": "https://example.com/result.mp4"},
                upstream_task_id="provider-task-1",
                upstream_status="completed",
            )
            due = gateway.store.due_webhook_events(limit=10, lease_seconds=10)
            self.assertEqual(len(due), 1)
            event = json.loads(due[0]["payload_json"])
            self.assertEqual(event["event_type"], "video.task.succeeded")
            self.assertEqual(event["data"]["result_delivery"], "pending_settlement")
            self.assertEqual(event["data"]["billing"]["status"], "settlement_pending")
            original = due[0]["payload_json"]
            gateway.store.retry_webhook(due[0]["event_id"], delay_seconds=5, error="HTTP 503")
            with gateway.store.connect() as connection:
                connection.execute(
                    "update video_webhook_outbox set next_attempt_at=0 where event_id=?",
                    (due[0]["event_id"],),
                )
            replay = gateway.store.due_webhook_events(limit=10, lease_seconds=10)[0]
            self.assertEqual(replay["payload_json"], original)
            self.assertEqual(replay["attempts"], 2)

    def test_webhook_signature_uses_exact_persisted_body(self):
        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"{}"

        class Opener:
            request = None

            def open(self, request, timeout):
                self.request = request
                return Response()

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory, webhook=True)
            created, _ = gateway.submit_v2(self.body(), idempotency_key="billing-v2-request")
            job_id = created["job_id"]
            self.assertIsNotNone(gateway.store.claim_submit(job_id))
            gateway.store.finish(job_id, "failed", error={"code": "provider_rejected"})
            event = gateway.store.due_webhook_events(limit=1, lease_seconds=10)[0]
            opener = Opener()
            with mock.patch("app.urllib.request.build_opener", return_value=opener):
                gateway._deliver_webhook(event)
            request = opener.request
            timestamp = request.get_header("X-xingtu-timestamp")
            expected = "v1=" + hmac.new(
                gateway.config.webhook_secret.encode(),
                timestamp.encode("ascii") + b"." + request.data,
                hashlib.sha256,
            ).hexdigest()
            self.assertTrue(hmac.compare_digest(request.get_header("X-xingtu-signature"), expected))
            self.assertEqual(request.get_header("X-xingtu-contract-version"), BILLING_CONTRACT_VERSION)
            with gateway.store.connect() as connection:
                row = connection.execute(
                    "select status,attempts from video_webhook_outbox where event_id=?",
                    (event["event_id"],),
                ).fetchone()
            self.assertEqual(dict(row), {"status": "delivered", "attempts": 1})

    def test_http_v2_contract_returns_authoritative_root_object(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = self.make_gateway(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class(gateway))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/v1/videos"
                encoded = json.dumps(self.body(), separators=(",", ":")).encode()
                headers = {
                    "Authorization": "Bearer test-token",
                    "Content-Type": "application/json",
                    "X-XingTu-Contract-Version": BILLING_CONTRACT_VERSION,
                    "Idempotency-Key": "billing-v2-request",
                }
                with urllib.request.urlopen(
                    urllib.request.Request(url, data=encoded, headers=headers, method="POST"),
                    timeout=5,
                ) as response:
                    self.assertEqual(response.status, 202)
                    created = json.loads(response.read())
                self.assertEqual(created["object"], "video")
                self.assertEqual(created["request_id"], "billing-v2-request")
                self.assertEqual(created["billing"]["reserved_amount"], "5.961600")
                task_url = f"{url}/{created['id']}"
                with urllib.request.urlopen(
                    urllib.request.Request(
                        task_url,
                        headers={
                            "Authorization": "Bearer test-token",
                            "X-XingTu-Contract-Version": BILLING_CONTRACT_VERSION,
                        },
                    ),
                    timeout=5,
                ) as response:
                    queried = json.loads(response.read())
                self.assertEqual(queried["id"], created["id"])
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            url,
                            data=encoded,
                            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=5,
                    )
                self.assertEqual(failure.exception.code, 400)

                legacy_headers = {
                    "Authorization": "Bearer test-token",
                    "Content-Type": "application/json",
                    "X-XingTu-Contract-Version": BILLING_CONTRACT_LEGACY,
                    "Idempotency-Key": "billing-v2-request",
                }
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(
                        urllib.request.Request(url, data=encoded, headers=legacy_headers, method="POST"),
                        timeout=5,
                    )
                self.assertEqual(failure.exception.code, 400)
                error = json.loads(failure.exception.read())
                self.assertEqual(error["error"]["code"], "unsupported_contract_version")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
