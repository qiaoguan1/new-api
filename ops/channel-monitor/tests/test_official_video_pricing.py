import pathlib
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone


MODULE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from official_video_pricing import (  # noqa: E402
    OfficialVideoPricingError,
    build_official_model_price_plan,
    quote_video_sale,
    validate_official_video_pricing,
)


def catalog_fixture():
    now = datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "revision": "2026-08-09.1",
        "currency": "CNY",
        "markup": 1.5,
        "source_url": "https://www.volcengine.com/product/doubao",
        "source_checked_at": now.isoformat(timespec="seconds"),
        "valid_until": (now + timedelta(days=30)).isoformat(timespec="seconds"),
        "token_formula": {
            "frame_rate": 24,
            "divisor": 1024,
            "min_output_seconds": 4,
            "max_output_seconds": 15,
            "dimensions": {
                "480p": {"16:9": [854, 480], "9:16": [480, 854]},
                "720p": {"16:9": [1280, 720], "9:16": [720, 1280]},
                "1080p": {"16:9": [1920, 1080], "9:16": [1080, 1920]},
            },
        },
        "models": {
            "seedance-2.0": {
                "resolutions": ["480p", "720p", "1080p"],
                "cny_per_m_tokens_by_resolution": {
                    "480p": {"no_video_input": 46, "with_video_input": 28},
                    "720p": {"no_video_input": 46, "with_video_input": 28},
                    "1080p": {"no_video_input": 51, "with_video_input": 31},
                },
            },
            "seedance-2.0-fast": {
                "resolutions": ["480p", "720p"],
                "cny_per_m_tokens_by_resolution": {
                    "480p": {"no_video_input": 37, "with_video_input": 22},
                    "720p": {"no_video_input": 37, "with_video_input": 22},
                },
            },
            "seedance-2.0-mini": {
                "resolutions": ["480p", "720p"],
                "cny_per_m_tokens_by_resolution": {
                    "480p": {"no_video_input": 23, "with_video_input": 14},
                    "720p": {"no_video_input": 23, "with_video_input": 14},
                },
            },
        },
    }


class OfficialVideoPricingTests(unittest.TestCase):
    def test_full_720p_four_second_quote_is_official_times_one_point_five(self):
        catalog = validate_official_video_pricing(catalog_fixture())

        quote = quote_video_sale(
            catalog,
            model="seedance-2.0",
            resolution="720p",
            output_seconds=4,
            aspect_ratio="16:9",
        )

        self.assertEqual(quote["estimated_tokens"], 86_400)
        self.assertEqual(quote["official_cost_cny"], 3.9744)
        self.assertEqual(quote["sale_cny"], 5.9616)
        self.assertEqual(quote["sale_cny_per_second"], 1.4904)
        self.assertEqual(quote["pricing_revision"], "2026-08-09.1")

    def test_each_variant_uses_its_own_official_rate(self):
        catalog = validate_official_video_pricing(catalog_fixture())

        expected = {
            "seedance-2.0": 1.4904,
            "seedance-2.0-fast": 1.1988,
            "seedance-2.0-mini": 0.7452,
        }
        for model, sale_per_second in expected.items():
            with self.subTest(model=model):
                quote = quote_video_sale(
                    catalog,
                    model=model,
                    resolution="720p",
                    output_seconds=4,
                    aspect_ratio="16:9",
                )
                self.assertEqual(quote["sale_cny_per_second"], sale_per_second)

    def test_full_1080p_uses_its_distinct_fifty_one_yuan_rate(self):
        catalog = validate_official_video_pricing(catalog_fixture())

        quote = quote_video_sale(
            catalog,
            model="seedance-2.0",
            resolution="1080p",
            output_seconds=4,
            aspect_ratio="16:9",
        )

        self.assertEqual(quote["estimated_tokens"], 194_400)
        self.assertEqual(quote["official_cost_cny"], 9.9144)
        self.assertEqual(quote["sale_cny"], 14.8716)
        self.assertEqual(quote["sale_cny_per_second"], 3.7179)

    def test_video_input_fails_closed_without_minimum_token_table(self):
        catalog = validate_official_video_pricing(catalog_fixture())

        with self.assertRaisesRegex(OfficialVideoPricingError, "minimum-token"):
            quote_video_sale(
                catalog,
                model="seedance-2.0",
                resolution="720p",
                output_seconds=4,
                aspect_ratio="16:9",
                input_video_seconds=2,
            )

    def test_unknown_specs_and_out_of_range_duration_fail_closed(self):
        catalog = validate_official_video_pricing(catalog_fixture())
        cases = [
            {"model": "unknown", "resolution": "720p", "output_seconds": 4},
            {"model": "seedance-2.0-fast", "resolution": "1080p", "output_seconds": 4},
            {"model": "seedance-2.0", "resolution": "720p", "output_seconds": 3},
        ]
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(OfficialVideoPricingError):
                    quote_video_sale(catalog, aspect_ratio="16:9", **values)

    def test_catalog_rejects_non_ark_source_and_resolution_rate_gaps(self):
        bad_source = deepcopy(catalog_fixture())
        bad_source["source_url"] = "https://example.test/pricing"
        with self.assertRaises(OfficialVideoPricingError):
            validate_official_video_pricing(bad_source)

        missing_1080 = deepcopy(catalog_fixture())
        del missing_1080["models"]["seedance-2.0"][
            "cny_per_m_tokens_by_resolution"
        ]["1080p"]
        with self.assertRaises(OfficialVideoPricingError):
            validate_official_video_pricing(missing_1080)

        wrong_formula = deepcopy(catalog_fixture())
        wrong_formula["token_formula"]["frame_rate"] = 25
        with self.assertRaisesRegex(OfficialVideoPricingError, "Ark formula"):
            validate_official_video_pricing(wrong_formula)

    def test_option_plan_uses_official_rate_not_upstream_cost(self):
        catalog = validate_official_video_pricing(catalog_fixture())
        routes = [
            {
                "raw_model": "seedance2.0-selfsur-720p",
                "stable_model": "seedance-2.0",
                "resolution": "720p",
            }
        ]
        options = {
            "ModelRatio": {"seedance2.0-selfsur-720p": 99},
            "CompletionRatio": {"seedance2.0-selfsur-720p": 2},
            "ModelPrice": {"seedance2.0-selfsur-720p": 0.168387},
            "GroupRatio": {"default": 0.15},
        }

        plan = build_official_model_price_plan(catalog, routes, options)

        decision = plan["decisions"][0]
        self.assertEqual(decision["official_cost_cny_per_second"], 0.9936)
        self.assertEqual(decision["sale_cny_per_second"], 1.4904)
        self.assertEqual(decision["new_model_price"], 9.936)
        self.assertEqual(
            plan["options"]["ModelPrice"]["seedance2.0-selfsur-720p"], 9.936
        )
        self.assertNotIn("seedance2.0-selfsur-720p", plan["options"]["ModelRatio"])
        self.assertNotIn("seedance2.0-selfsur-720p", plan["options"]["CompletionRatio"])

    def test_option_plan_always_prices_every_canonical_official_sku(self):
        catalog = validate_official_video_pricing(catalog_fixture())
        expected_model_prices = {
            "seedance-2.0-480p": 4.41945,
            "seedance-2.0-720p": 9.936,
            "seedance-2.0-1080p": 24.786,
            "seedance-2.0-fast-480p": 3.554775,
            "seedance-2.0-fast-720p": 7.992,
            "seedance-2.0-mini-480p": 2.209725,
            "seedance-2.0-mini-720p": 4.968,
        }
        options = {
            "ModelRatio": dict.fromkeys(expected_model_prices, 99),
            "CompletionRatio": dict.fromkeys(expected_model_prices, 2),
            "ModelPrice": {},
            "GroupRatio": {"default": 0.15},
        }

        plan = build_official_model_price_plan(catalog, [], options)

        self.assertEqual(plan["options"]["ModelPrice"], expected_model_prices)
        self.assertTrue(
            expected_model_prices.keys().isdisjoint(plan["options"]["ModelRatio"])
        )
        self.assertTrue(
            expected_model_prices.keys().isdisjoint(
                plan["options"]["CompletionRatio"]
            )
        )
        self.assertEqual(
            {row["model"] for row in plan["decisions"]},
            set(expected_model_prices),
        )


if __name__ == "__main__":
    unittest.main()
