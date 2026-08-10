import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import AdapterError, Observation, ProviderConfig
from app import Gateway, GatewayError, _validate_result_url
from catalog import Catalog, Model, Route
from store import Store


class FakeAdapter:
    def __init__(self, provider_id, outcome):
        self.config = ProviderConfig(provider_id, "https://example.com", "secret", ("example.com",))
        self.outcome = outcome
        self.calls = 0
        self.last_payload = None

    @property
    def ready_for_new_jobs(self):
        return self.config.configured

    def submit(self, request_id, upstream_model, payload):
        self.calls += 1
        self.last_payload = payload
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class GatewayFallbackTests(unittest.TestCase):
    def test_toonflow_result_allowlist_accepts_tos_subdomain_only(self):
        allowed = ("api.toonflow.net", "tos-cn-beijing.volces.com")
        with mock.patch("app.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            host = _validate_result_url(
                "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/result.mp4",
                allowed,
            )
        self.assertEqual(host, "ark-acg-cn-beijing.tos-cn-beijing.volces.com")
        with self.assertRaises(GatewayError):
            _validate_result_url(
                "https://tos-cn-beijing.volces.com.attacker.example/result.mp4",
                allowed,
            )

    def make_job(self, store):
        snapshot, _ = store.create(
            request_id="request-fallback",
            fingerprint="fingerprint",
            protocol_version="xtai-relay-v1",
            catalog_revision="test",
            stable_model="seedance-2.0",
            provider_id="toonflow",
            upstream_model="Seedance 2.0",
            adapter_revision="toonflow-v1",
            payload_json='{"_route":{"resolution":"720p"}}',
            route_plan=[
                {"provider_id": "toonflow", "upstream_model": "Seedance 2.0", "adapter_revision": "toonflow-v1", "send_resolution": True},
                {"provider_id": "paisio", "upstream_model": "sd2-720p", "adapter_revision": "paisio-v1", "send_resolution": False},
            ],
            selection_reason="test",
        )
        return str(snapshot["job_id"])

    def gateway(self, store, toonflow, paisio):
        gateway = Gateway.__new__(Gateway)
        gateway.store = store
        gateway.adapters = {"toonflow": toonflow, "paisio": paisio}
        gateway.config = types.SimpleNamespace(poll_interval_seconds=5)
        return gateway

    def test_definitive_precreation_failure_uses_next_persisted_route(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.make_job(store)
            first = FakeAdapter("toonflow", Observation(status="failed", error_code="rejected"))
            second = FakeAdapter("paisio", Observation(status="running", upstream_task_id="paisio-1", upstream_status="queued"))

            self.gateway(store, first, second)._submit_with_fallback(job_id)

            job = store.get(job_id=job_id, internal=True)
            self.assertEqual(job["status"], "running")
            self.assertEqual(job["provider_id"], "paisio")
            self.assertEqual(first.calls, 1)
            self.assertEqual(second.calls, 1)

    def test_uncertain_submit_never_crosses_provider(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.make_job(store)
            first = FakeAdapter(
                "toonflow",
                AdapterError("timeout", "unknown", phase="submit", uncertain=True),
            )
            second = FakeAdapter("paisio", Observation(status="running", upstream_task_id="paisio-1"))

            self.gateway(store, first, second)._submit_with_fallback(job_id)

            job = store.get(job_id=job_id, internal=True)
            self.assertEqual(job["status"], "uncertain")
            self.assertEqual(job["provider_id"], "toonflow")
            self.assertEqual(second.calls, 0)

    def test_legacy_queued_job_keeps_its_persisted_send_resolution_flag(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            snapshot, _ = store.create(
                request_id="legacy-request",
                fingerprint="legacy-fingerprint",
                protocol_version="xtai-relay-v1",
                catalog_revision="legacy",
                stable_model="seedance-2.0",
                provider_id="toonflow",
                upstream_model="Seedance 2.0",
                adapter_revision="legacy",
                payload_json='{"_route":{"resolution":"720p","send_resolution":true}}',
            )
            connection = store.connect()
            connection.execute(
                "update video_jobs set route_plan_json='[]' where job_id=?",
                (snapshot["job_id"],),
            )
            connection.close()
            adapter = FakeAdapter(
                "toonflow",
                Observation(status="running", upstream_task_id="legacy-upstream", upstream_status="queued"),
            )

            self.gateway(store, adapter, FakeAdapter("paisio", Observation(status="failed")))._submit_with_fallback(
                str(snapshot["job_id"])
            )

            self.assertTrue(adapter.last_payload["_route"]["send_resolution"])

    def test_request_constraints_remove_only_incompatible_route(self):
        constrained = Route(
            provider="toonflow",
            upstream_model="Seedance 2.0",
            priority=10,
            enabled=True,
            adapter_revision="toonflow-v1",
            resolution="720p",
            aspect_ratios=("16:9",),
        )
        compatible = Route(
            provider="paisio",
            upstream_model="sd2-720p",
            priority=10,
            enabled=True,
            adapter_revision="paisio-v1",
            resolution="720p",
        )
        model = Model(
            id="seedance-2.0",
            label="full",
            enabled=True,
            operation_modes=("text",),
            aspect_ratios=("16:9", "9:16"),
            durations=(),
            duration_min=4,
            duration_max=15,
            max_images=1,
            max_videos=0,
            routes=(constrained, compatible),
            resolutions=("720p",),
        )
        gateway = Gateway.__new__(Gateway)
        gateway.catalog = Catalog("xtai-relay-v1", "test", (model,))
        gateway.adapters = {
            "toonflow": types.SimpleNamespace(ready_for_new_jobs=True),
            "paisio": types.SimpleNamespace(ready_for_new_jobs=True),
        }
        gateway.pricing = types.SimpleNamespace(quote=lambda *_: {"amount": "1.0"})

        _, _, _, _, routes = gateway.validate_payload(
            {
                "protocol_version": "xtai-relay-v1",
                "capability": "video.generate",
                "request_id": "constraint-request",
                "model": "seedance-2.0",
                "input": {"prompt": "test"},
                "parameters": {
                    "resolution": "720p",
                    "duration": 5,
                    "aspect_ratio": "9:16",
                    "mode": "text",
                },
            }
        )

        self.assertEqual([route.provider for route in routes], ["paisio"])

    def test_public_readiness_is_redacted_but_authenticated_health_has_provider_rows(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            gateway = Gateway.__new__(Gateway)
            gateway.store = Store(pathlib.Path(directory))
            gateway.catalog = types.SimpleNamespace(protocol_version="xtai-relay-v1", revision="test")
            gateway.adapters = {
                "toonflow": FakeAdapter("toonflow", Observation(status="running")),
                "paisio": FakeAdapter("paisio", Observation(status="running")),
            }
            gateway.config = types.SimpleNamespace(
                data_dir=pathlib.Path(directory),
                drain_file_name="DRAIN",
                uncertainty_window_seconds=600,
                uncertainty_count_threshold=2,
                uncertainty_rate_min_samples=20,
                uncertainty_rate_percent=1.0,
            )
            gateway.stream_snapshot = lambda: {"active": 0}

            ready, public = gateway.readiness()
            internal = gateway.provider_health()

            self.assertTrue(ready)
            public_text = str(public).lower()
            self.assertNotIn("toonflow", public_text)
            self.assertNotIn("paisio", public_text)
            self.assertEqual(
                {row["provider_id"] for row in internal["providers"]},
                {"toonflow", "paisio"},
            )


if __name__ == "__main__":
    unittest.main()
