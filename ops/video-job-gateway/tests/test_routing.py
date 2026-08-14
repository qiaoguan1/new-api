import pathlib
import sys
import unittest
from collections import Counter


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog import Catalog, Model, Route
from routing import RoutePlanError, build_route_plan


class RoutePlanTests(unittest.TestCase):
    def test_container_image_includes_routing_module(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        copy_line = next(line for line in dockerfile.splitlines() if line.startswith("COPY "))
        self.assertIn("routing.py", copy_line.split())

    def route(self, provider: str, *, priority: int = 10, resolution: str = "720p") -> Route:
        return Route(
            provider=provider,
            upstream_model=f"{provider}-model",
            priority=priority,
            enabled=True,
            adapter_revision=f"{provider}-v1",
            resolution=resolution,
            send_resolution=True,
        )

    def test_equal_priority_routes_are_deterministic_and_both_receive_traffic(self):
        routes = (self.route("toonflow"), self.route("paisio"))
        first_choices = []

        for index in range(100):
            request_id = f"request-{index:03d}"
            first = build_route_plan(
                request_id=request_id,
                stable_model="seedance-2.0",
                resolution="720p",
                routes=routes,
            )[0]
            repeated = build_route_plan(
                request_id=request_id,
                stable_model="seedance-2.0",
                resolution="720p",
                routes=tuple(reversed(routes)),
            )[0]
            self.assertEqual(first, repeated)
            first_choices.append(first.provider)

        counts = Counter(first_choices)
        self.assertEqual(set(counts), {"toonflow", "paisio"})
        self.assertGreaterEqual(counts["toonflow"], 40)
        self.assertLessEqual(counts["toonflow"], 60)
        self.assertGreaterEqual(counts["paisio"], 40)
        self.assertLessEqual(counts["paisio"], 60)

    def test_lower_priority_number_is_ordered_before_fallback_tier(self):
        plan = build_route_plan(
            request_id="request-priority",
            stable_model="seedance-2.0",
            resolution="720p",
            routes=(self.route("toonflow", priority=20), self.route("paisio", priority=10)),
        )

        self.assertEqual([route.provider for route in plan], ["paisio", "toonflow"])

    def test_plan_rejects_empty_or_duplicate_provider_routes(self):
        with self.assertRaisesRegex(RoutePlanError, "eligible"):
            build_route_plan(
                request_id="request-empty",
                stable_model="seedance-2.0",
                resolution="720p",
                routes=(),
            )

        duplicate = self.route("toonflow")
        with self.assertRaisesRegex(RoutePlanError, "duplicate"):
            build_route_plan(
                request_id="request-duplicate",
                stable_model="seedance-2.0",
                resolution="720p",
                routes=(duplicate, duplicate),
            )

    def test_catalog_returns_every_configured_route_for_requested_resolution(self):
        model = Model(
            id="seedance-2.0",
            label="full",
            enabled=True,
            operation_modes=("text",),
            aspect_ratios=("16:9",),
            durations=(),
            duration_min=4,
            duration_max=15,
            max_images=1,
            max_videos=0,
            resolutions=("480p", "720p"),
            aliases=(),
            routes=(
                self.route("toonflow", resolution="480p"),
                self.route("toonflow", resolution="720p"),
                self.route("paisio", resolution="720p"),
            ),
        )
        catalog = Catalog("xtai-relay-v1", "test", (model,))

        resolved, routes, resolution, legacy = catalog.resolve_routes(
            "seedance-2.0", "720p", {"toonflow", "paisio"}
        )

        self.assertIs(resolved, model)
        self.assertEqual(resolution, "720p")
        self.assertFalse(legacy)
        self.assertEqual({route.provider for route in routes}, {"toonflow", "paisio"})

        _, routes, _, _ = catalog.resolve_routes(
            "seedance-2.0", "480p", {"toonflow", "paisio"}
        )
        self.assertEqual([route.provider for route in routes], ["toonflow"])

    def test_checked_in_catalog_routes_full_and_fast_to_both_video_upstreams(self):
        catalog = Catalog.load(ROOT / "catalog.json")

        expected = {
            "seedance-2.0": ("480p", "720p", "1080p"),
            "seedance-2.0-fast": ("480p", "720p"),
        }
        for model_id, resolutions in expected.items():
            for resolution in resolutions:
                _, routes, _, _ = catalog.resolve_routes(
                    model_id,
                    resolution,
                    {"toonflow", "paisio"},
                )
                self.assertEqual({route.provider for route in routes}, {"toonflow", "paisio"})

        _, mini_routes, _, _ = catalog.resolve_routes(
            "seedance-2.0-mini",
            "720p",
            {"toonflow", "paisio"},
        )
        self.assertEqual([route.provider for route in mini_routes], ["toonflow"])

    def test_checked_in_catalog_prefers_paisio_before_toonflow(self):
        catalog = Catalog.load(ROOT / "catalog.json")

        shared = {
            "seedance-2.0": ("480p", "720p", "1080p"),
            "seedance-2.0-fast": ("480p", "720p"),
        }
        for model_id, resolutions in shared.items():
            for resolution in resolutions:
                _, routes, _, _ = catalog.resolve_routes(
                    model_id,
                    resolution,
                    {"toonflow", "paisio"},
                )
                for index in range(100):
                    plan = build_route_plan(
                        request_id=f"{model_id}-{resolution}-{index:03d}",
                        stable_model=model_id,
                        resolution=resolution,
                        routes=tuple(reversed(routes)) if index % 2 else routes,
                        duration=5,
                    )
                    self.assertEqual(plan[0].provider, "paisio")
                    self.assertEqual(plan[-1].provider, "toonflow")

    def test_checked_in_catalog_uses_current_face_capable_paisio_sd3_sd4_models(self):
        catalog = Catalog.load(ROOT / "catalog.json")
        expected = {
            ("seedance-2.0", "480p"): ["sd3-480p"],
            ("seedance-2.0", "720p"): ["sd3-720p"],
            ("seedance-2.0", "1080p"): ["sd3-1080p"],
            ("seedance-2.0-fast", "480p"): ["sd3-fast-480p"],
            ("seedance-2.0-fast", "720p"): [
                "sd3-fast-720p",
                "sd4-fast2-720p",
                "sd4-fast5-720p",
            ],
        }

        for (model_id, resolution), upstream_models in expected.items():
            _, routes, _, _ = catalog.resolve_routes(
                model_id,
                resolution,
                {"paisio"},
            )
            self.assertEqual([route.upstream_model for route in routes], upstream_models)
            self.assertTrue(all(route.provider == "paisio" for route in routes))
            self.assertTrue(
                all(
                    route.adapter_revision == "paisio-video-face-sd3-sd4-2026-08-13"
                    for route in routes
                )
            )

    def test_fast_720p_chooses_per_second_or_per_call_by_duration(self):
        catalog = Catalog.load(ROOT / "catalog.json")
        _, routes, _, _ = catalog.resolve_routes(
            "seedance-2.0-fast",
            "720p",
            {"paisio", "toonflow"},
        )

        expected_first = {
            4: "sd3-fast-720p",
            6: "sd3-fast-720p",
            7: "sd4-fast2-720p",
            15: "sd4-fast2-720p",
        }
        for duration, first_model in expected_first.items():
            plan = build_route_plan(
                request_id=f"cost-route-{duration}",
                stable_model="seedance-2.0-fast",
                resolution="720p",
                routes=routes,
                duration=duration,
            )
            self.assertEqual(plan[0].upstream_model, first_model)
            self.assertEqual(plan[0].provider, "paisio")
            self.assertEqual(plan[-1].provider, "toonflow")
            self.assertLess(
                [route.upstream_model for route in plan].index("sd4-fast2-720p"),
                [route.upstream_model for route in plan].index("sd4-fast5-720p"),
            )

    def test_production_catalog_and_pricing_do_not_expose_sd2_names(self):
        for name in ("catalog.json", "relay-pricing.json"):
            raw = (ROOT / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("sd2", raw, name)

        catalog = Catalog.load(ROOT / "catalog.json")
        self.assertEqual(catalog.revision, "2026-08-14.2")

    def test_paisio_route_names_follow_standard_fast_mini_contract(self):
        catalog = Catalog.load(ROOT / "catalog.json")
        full = next(model for model in catalog.models if model.id == "seedance-2.0")
        fast = next(model for model in catalog.models if model.id == "seedance-2.0-fast")
        mini = next(model for model in catalog.models if model.id == "seedance-2.0-mini")

        full_names = [route.upstream_model.lower() for route in full.routes if route.provider == "paisio"]
        fast_names = [route.upstream_model.lower() for route in fast.routes if route.provider == "paisio"]
        mini_names = [route.upstream_model.lower() for route in mini.routes if route.provider == "paisio"]

        self.assertTrue(full_names)
        self.assertTrue(all("fast" not in name and "mini" not in name for name in full_names))
        self.assertTrue(fast_names)
        self.assertTrue(all("fast" in name and "mini" not in name for name in fast_names))
        self.assertEqual(mini_names, [])

    def test_checked_in_routes_isolate_reference_audio_to_verified_toonflow(self):
        catalog = Catalog.load(ROOT / "catalog.json")
        for model in catalog.models:
            self.assertTrue(any(route.supports_reference_audio for route in model.routes))
            self.assertTrue(
                all(
                    route.provider == "toonflow"
                    for route in model.routes
                    if route.supports_reference_audio
                )
            )
            self.assertTrue(
                all(route.supports_reference_video for route in model.routes if route.provider == "toonflow")
            )


if __name__ == "__main__":
    unittest.main()
