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

    def test_checked_in_catalog_always_prefers_paisio_for_shared_capabilities(self):
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
                    )
                    self.assertEqual(
                        [route.provider for route in plan],
                        ["paisio", "toonflow"],
                    )


if __name__ == "__main__":
    unittest.main()
