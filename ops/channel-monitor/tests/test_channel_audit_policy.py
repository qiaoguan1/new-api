import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "channel_audit_policy.py"
SPEC = importlib.util.spec_from_file_location("channel_audit_policy", MODULE_PATH)
POLICY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(POLICY)


class ChannelAuditPolicyTests(unittest.TestCase):
    def test_catalog_is_intersected_with_configured_models_only(self):
        channel = {"models": "gpt-5.5,gpt-image-2", "model_mapping": "{}"}
        catalog = [{"model_name": f"catalog-{index}"} for index in range(700)]
        catalog.extend([{"model_name": "gpt-5.5"}, {"model_name": "gpt-image-2"}])
        result = POLICY.intersect_pricing_catalog(channel, catalog)
        self.assertEqual(
            [item["local_model"] for item in result], ["gpt-5.5", "gpt-image-2"]
        )
        self.assertTrue(all(item["pricing"] is not None for item in result))

    def test_mapping_uses_upstream_alias_but_preserves_local_name(self):
        channel = {
            "models": "friendly-image",
            "model_mapping": '{"friendly-image":"gpt-image-2"}',
        }
        result = POLICY.intersect_pricing_catalog(
            channel, [{"model_name": "gpt-image-2", "model_ratio": 3}]
        )
        self.assertEqual(result, [{
            "local_model": "friendly-image",
            "upstream_model": "gpt-image-2",
            "pricing": {"model_name": "gpt-image-2", "model_ratio": 3},
        }])

    def test_missing_catalog_model_stays_in_inventory_without_guessed_price(self):
        result = POLICY.intersect_pricing_catalog({"models": "private-model"}, [])
        self.assertEqual(result, [{
            "local_model": "private-model",
            "upstream_model": "private-model",
            "pricing": None,
        }])

    def test_image_only_channel_selects_image_model_and_endpoint(self):
        channel = {"name": "maolao API 图", "group": "生图", "models": "gpt-image-2"}
        model = POLICY.select_probe_model(channel)
        self.assertEqual(model, "gpt-image-2")
        self.assertEqual(POLICY.probe_endpoint(model), "/v1/images/generations")
        self.assertEqual(POLICY.probe_body(model, "/v1/images/generations"), {
            "model": "gpt-image-2",
            "prompt": "a small white square",
            "n": 1,
        })

    def test_invalid_explicit_text_probe_cannot_override_image_only_configuration(self):
        channel = {
            "name": "codeplan图",
            "group": "图片分组-4k",
            "models": "gpt-image-2",
            "test_model": "gpt-5.5",
        }
        self.assertEqual(POLICY.select_probe_model(channel), "gpt-image-2")

    def test_valid_explicit_probe_is_preserved(self):
        channel = {"models": "gpt-5.5,gpt-5.6-sol", "test_model": "gpt-5.6-sol"}
        self.assertEqual(POLICY.select_probe_model(channel), "gpt-5.6-sol")

    def test_local_alias_probe_uses_upstream_model(self):
        channel = {
            "models": "friendly-image",
            "model_mapping": '{"friendly-image":"gpt-image-2"}',
            "test_model": "friendly-image",
        }
        self.assertEqual(POLICY.select_probe_model(channel), "gpt-image-2")

    def test_metadata_probe_intersects_configured_advertised_priced_and_group(self):
        channel = {
            "models": "unsupported,video-pro-720p,gpt-5.6-sol",
            "model_mapping": "{}",
        }
        pricing_rows = [
            {"model_name": "unsupported", "enable_groups": ["other"]},
            {"model_name": "video-pro-720p", "enable_groups": ["default"]},
            {"model_name": "gpt-5.6-sol", "enable_groups": ["default"]},
        ]

        selected = POLICY.select_metadata_probe_model(
            channel,
            advertised_models={"unsupported", "video-pro-720p", "gpt-5.6-sol"},
            pricing_rows=pricing_rows,
            upstream_group="default",
            account_models={"video-pro-720p", "gpt-5.6-sol"},
        )

        self.assertEqual(selected, "video-pro-720p")

    def test_metadata_probe_requires_account_model_visibility(self):
        selected = POLICY.select_metadata_probe_model(
            {"models": "video-pro-720p"},
            advertised_models={"video-pro-720p"},
            pricing_rows=[{
                "model_name": "video-pro-720p",
                "enable_groups": ["default"],
            }],
            upstream_group="default",
            account_models={"some-other-model"},
        )

        self.assertEqual(selected, "")

    def test_metadata_probe_accepts_comma_separated_enable_groups(self):
        selected = POLICY.select_metadata_probe_model(
            {"models": "video-pro-720p"},
            advertised_models={"video-pro-720p"},
            pricing_rows=[{
                "model_name": "video-pro-720p",
                "enable_groups": "default,vip",
            }],
            upstream_group="default",
            account_models={"video-pro-720p"},
        )

        self.assertEqual(selected, "video-pro-720p")

    def test_account_visibility_overrides_unrelated_internal_group_labels(self):
        selected = POLICY.select_metadata_probe_model(
            {"models": "gpt-5.6-sol"},
            advertised_models={"gpt-5.6-sol"},
            pricing_rows=[{
                "model_name": "gpt-5.6-sol",
                "enable_groups": ["CodexPro"],
            }],
            upstream_group="default",
            account_models={"gpt-5.6-sol"},
        )

        self.assertEqual(selected, "gpt-5.6-sol")

    def test_metadata_probe_fails_closed_without_intersection(self):
        selected = POLICY.select_metadata_probe_model(
            {"models": "video-pro-720p"},
            advertised_models={"video-pro-720p"},
            pricing_rows=[],
            upstream_group="default",
        )

        self.assertEqual(selected, "")



if __name__ == "__main__":
    unittest.main()
