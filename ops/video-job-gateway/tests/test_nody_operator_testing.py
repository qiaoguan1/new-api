"""Operator testing is honestly estimated, bounded and never fake acceptance."""

import copy
import json
import pathlib
import sys
import tempfile
import unittest
from dataclasses import replace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog import Catalog
from nody_operator_testing import NodyOperatorTesting, SOURCE, validate_operator_quote
from nody_media_wire import build_candidate_body
from nodyhub import NODY_MODELS, NodyHubAdapter
from adapters import ProviderConfig, JsonResponse


def policy(**changes):
    result = {"schema_version": "xtai-nody-operator-testing-v1", "revision": "operator-fixture", "enabled": True,
              "source_snapshot": {"endpoint": "https://nodyhub.com/api/pricing", "observed_at": "2026-10-08T01:00:00Z",
                                  "sha256": "a" * 64, "source_unit": "display_credit", "billing_unit": "per_second"},
              "currency_factor_exact": "1.500000", "group_factor_exact": "2.000000", "markup_exact": "1.500000",
              "max_reserve_cny_exact": "150.000000", "models": [{"model": model, "max_source_rate_exact": "0.200000", "source_row_sha256": "b" * 64} for model in NODY_MODELS]}
    result.update(changes)
    return result


def media_payload(**changes):
    result = {"model": "wan3.0-video", "resolution": "720p", "duration": 6, "mode": "all_reference", "prompt": "a blue ball",
              "aspect_ratio": "16:9", "generate_audio": True, "_nody_operator_testing": True,
              "images": [{"url": "https://media.example/a.png", "role": "reference", "identity": "c" * 64}],
              "videos": [{"url": "https://media.example/a.mp4", "role": "reference", "identity": "d" * 64}], "audios": [],
              "input_video_seconds_exact": "5.000000", "reference_input": {"reference_videos": [{"sha256": "d" * 64, "duration_seconds": "5.000000"}], "reference_audios": []}}
    result.update(changes)
    return result


def quoted(operator, payload):
    return operator.quote(payload["model"], payload["resolution"], payload["duration"], payload["mode"], len(payload["images"]), len(payload["videos"]), len(payload["audios"]),
                          payload["generate_audio"], input_video_seconds_exact=payload.get("input_video_seconds_exact"),
                          input_audio_seconds_exact=payload.get("input_audio_seconds_exact"), aspect_ratio=payload["aspect_ratio"])


class OperatorTests(unittest.TestCase):
    def load(self, data=None):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "operator.json"
            path.write_text(json.dumps(policy() if data is None else data), encoding="utf-8")
            return NodyOperatorTesting.load(path)

    def test_missing_and_disabled_policy_have_no_admission_or_catalog_effect(self):
        base = Catalog.load(ROOT / "catalog.json")
        for operator in (NodyOperatorTesting.load(None), self.load({"schema_version": "xtai-nody-operator-testing-v1", "revision": "disabled", "enabled": False})):
            self.assertFalse(operator.enabled)
            self.assertEqual(operator.public_rules(), [])
            self.assertEqual(operator.catalog(base).models, base.models)
            self.assertEqual(operator.catalog(base).revision, base.revision)
            with self.assertRaises(ValueError):
                quoted(operator, media_payload())

    def test_quote_is_explicit_estimate_not_fake_bill_and_binds_complete_tuple(self):
        operator = self.load()
        payload = media_payload()
        quote = quoted(operator, payload)
        self.assertEqual(quote["estimated_cost_cny_exact"], "3.600000")
        self.assertEqual(quote["amount_cny_exact"], "5.400000")
        self.assertEqual(quote["cny_per_second_exact"], "0.900000")
        self.assertEqual(quote["price_source"], SOURCE)
        self.assertEqual(quote["pricing_kind"], "estimated_reservation")
        self.assertEqual(quote["verification_status"], "unverified")
        self.assertEqual(quote["admission_mode"], "operator_testing")
        self.assertFalse(quote["is_upper_bound"])
        self.assertFalse(quote["reserve_cap_applied"])
        self.assertEqual(quote["input_video_seconds_exact"], "5.000000")
        self.assertEqual(quote["policy_digest"], quote["operator_policy_evidence"]["sha256"])
        for fake_bill in ("reference_cost_cny_exact", "official_cost_cny_exact", "media_contract_evidence", "image_contract_evidence", "evidence_task_id", "cost_status", "status"):
            self.assertNotIn(fake_bill, quote)
        self.assertTrue(validate_operator_quote(payload, quote))

    def test_max_reserve_is_capped_not_a_promised_final_charge_cap(self):
        data = policy()
        for row in data["models"]:
            row["max_source_rate_exact"] = "100.000000"
        operator = self.load(data)
        quote = quoted(operator, media_payload(duration=30))
        self.assertEqual(quote["amount_cny_exact"], "150.000000")
        self.assertEqual(quote["estimated_cost_cny_exact"], "9000.000000")
        self.assertTrue(quote["reserve_cap_applied"])
        self.assertFalse(quote["is_upper_bound"])
        self.assertTrue(validate_operator_quote(media_payload(duration=30), quote))

    def test_candidate_public_rules_are_ranges_not_fake_success_profiles_or_private_rates(self):
        rows = self.load().public_rules()
        self.assertEqual(len(rows), 7)
        by_model = {row["model"]: row for row in rows}
        wan = by_model["wan3.0-video"]
        self.assertIn("last_frame", wan["operation_modes"])
        self.assertEqual(wan["durations"], list(range(2, 31)))
        self.assertEqual((wan["max_images"], wan["max_videos"], wan["max_audios"]), (10, 5, 5))
        self.assertEqual(wan["input_video"]["max_total_duration_seconds"], 75)
        self.assertEqual(by_model["omni-flash"]["image_input_counts"], [0, 1, 3])
        self.assertEqual(by_model["omni-flash"]["durations"], [4, 6, 8, 10])
        self.assertEqual(by_model["omni-flash"]["video_input_output_duration_min"], 4)
        self.assertEqual(by_model["omni-flash"]["video_input_output_duration_max"], 30)
        self.assertEqual(by_model["grok-video-3"]["max_videos"], 0)
        self.assertEqual(by_model["flux-3-video"]["max_audios"], 0)
        self.assertEqual(wan["estimated_cny_per_output_second_exact"], "0.900000")
        serialized = json.dumps(rows)
        for field in ("max_source_rate_exact", "source_row_sha256", "source_snapshot", "operator_policy_evidence", "profiles", "succeeded", "evidence_task_id"):
            self.assertNotIn(field, serialized)

    def test_catalog_overlays_candidates_but_keeps_other_models_and_original_objects(self):
        base = Catalog.load(ROOT / "catalog.json")
        before = copy.deepcopy(base.models)
        result = self.load().catalog(base)
        self.assertEqual(base.models, before)
        self.assertIs(result.model("seedance-2.0"), base.model("seedance-2.0"))
        model = result.model("wan3.0-video")
        self.assertEqual(model.durations, tuple(range(2, 31)))
        self.assertEqual(model.resolutions, ("480p", "720p", "1080p"))
        self.assertEqual((model.max_images, model.max_videos), (10, 5))
        self.assertTrue(all(route.max_reference_audios == 5 for route in model.routes))
        self.assertEqual(result.revision, base.revision + "+operator-fixture")

    def test_catalog_widens_only_nody_route_aspect_constraints_for_candidates(self):
        base = Catalog.load(ROOT / "catalog.json")
        wan = base.model("wan3.0-video")
        narrow = replace(wan, routes=tuple(replace(route, aspect_ratios=("16:9",)) for route in wan.routes))
        base = Catalog(base.protocol_version, base.revision, tuple(narrow if model.id == wan.id else model for model in base.models))
        result = self.load().catalog(base)
        self.assertIn("1:1", result.model(wan.id).routes[0].aspect_ratios)
        self.assertEqual(base.model(wan.id).routes[0].aspect_ratios, ("16:9",))
        self.assertIs(result.model("seedance-2.0"), base.model("seedance-2.0"))

    def test_bad_policy_metadata_currency_group_and_duplicate_model_fail_closed(self):
        cases = [{"enabled": 1}, {"schema_version": "fake"}, {"currency_factor_exact": "2.000000"}, {"markup_exact": "2.000000"},
                 {"group_factor_exact": "0.000000"}, {"group_factor_exact": "NaN"}, {"max_reserve_cny_exact": "151.000000"},
                 {"revision": ""}, {"models": [{"model": "unknown", "max_source_rate_exact": "0.200000", "source_row_sha256": "b" * 64}]}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(policy(**changes))
        for changes in ({"endpoint": "https://evil.example/api/pricing"}, {"observed_at": "yesterday"}, {"sha256": "bad"}, {"billing_unit": "per_call"}, {"source_unit": "CNY"}):
            data = policy(); data["source_snapshot"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(data)
        data = policy(); data["models"].append(copy.deepcopy(data["models"][0]))
        with self.assertRaises(ValueError):
            self.load(data)

    def test_invalid_count_specs_and_missing_or_unbounded_input_seconds_never_quote(self):
        operator = self.load()
        args = ["wan3.0-video", "720p", 6, "all_reference", 1, 1, 0, True]
        for position, value in ((0, "unknown"), (1, "4k"), (2, True), (2, -1), (2, 31), (3, "arbitrary"), (4, 11), (5, 6), (6, True), (7, 1)):
            candidate = list(args); candidate[position] = value
            with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                operator.quote(*candidate, input_video_seconds_exact="5.000000")
        for value in (None, "NaN", "0.000000", "16.000000", "5", 5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                operator.quote(*args, input_video_seconds_exact=value)
        with self.assertRaises(ValueError):
            operator.quote("wan3.0-video", "720p", 6, "reference", 0, 0, 1, True)

    def test_frozen_estimate_validation_needs_no_live_policy_and_rejects_every_discriminator_change(self):
        payload = media_payload(); quote = quoted(self.load(), payload)
        for changes in ({"amount_cny_exact": "1.000000"}, {"verification_status": "verified"}, {"pricing_kind": "actual"},
                        {"policy_digest": "e" * 64}, {"input_video_seconds_exact": "6.000000"}, {"aspect_ratio": "1:1"},
                        {"video_count": 2}, {"generate_audio": False}, {"is_upper_bound": True}, {"reserve_cap_applied": True}):
            altered = {**quote, **changes}
            with self.subTest(changes=changes):
                self.assertFalse(validate_operator_quote(payload, altered))
        altered = copy.deepcopy(quote); altered["operator_policy_evidence"]["group_factor_exact"] = "3.000000"
        self.assertFalse(validate_operator_quote(payload, altered))
        altered_payload = copy.deepcopy(payload); altered_payload["reference_input"]["reference_videos"][0]["duration_seconds"] = "6.000000"
        self.assertFalse(validate_operator_quote(altered_payload, quote))
        self.assertTrue(validate_operator_quote(payload, quote))

    def test_candidate_text_builder_is_explicit_v2_and_legacy_baseline_adapter_bytes_stay_identical(self):
        for model, resolution, duration, expected in [
            ("wan3.0-video", "1080p", 30, {"resolution": "1080P", "size": "9:16", "audio": False, "watermark": False}),
            ("omni-flash", "4k", 10, {"resolution": "4k", "aspect_ratio": "9:16"}),
            ("flux-3-video", "1080p", 20, {"resolution": "1080p", "aspect_ratio": "9:16", "audio": False, "safety_tolerance": 4}),
            ("grok-video-3", "720p", 30, {"resolution": "720P", "ratio": "9:16"}),
            ("grok-imagine-1.5-video", "480p", 30, {"quality": "480p", "size": "9:16"}),
            ("grok-imagine-video-official", "720p", 15, {"resolution": "720p", "aspect_ratio": "9:16"}),
        ]:
            payload = {"model": model, "mode": "text", "prompt": "a blue ball", "resolution": resolution, "duration": duration,
                       "aspect_ratio": "9:16", "generate_audio": False if model in {"wan3.0-video", "flux-3-video"} else True, "images": [], "videos": [], "audios": []}
            with self.subTest(model=model):
                self.assertEqual(build_candidate_body(model, payload), {"model": model, "prompt": "a blue ball", "duration": duration, **expected})
        adapter = NodyHubAdapter(ProviderConfig("nodyhub", "https://nodyhub.com", "test", ()))
        for model, (resolution, duration, _) in NODY_MODELS.items():
            payload = {"mode": "text", "prompt": "a blue ball", "resolution": resolution, "duration": duration, "aspect_ratio": "16:9", "generate_audio": True}
            expected = {"model": model, "prompt": "a blue ball"}
            if model == "grok-video-3":
                expected.update(seconds="6", size="1280x720")
            else:
                expected.update(resolution=resolution, duration=duration, aspect_ratio="16:9")
            self.assertEqual(adapter.request_body(model, payload), expected)

    def test_operator_text_adapter_routes_v2_but_original_grok_text_still_routes_v1(self):
        class FakeTransport:
            def __init__(self):
                self.calls = []

            def request_json(self, method, url, **kwargs):
                self.calls.append((url, kwargs["payload"]))
                return JsonResponse(200, {}, {"id": "f38d6547-27af-4f3b-8945-8c643162c432", "status": "queued"}, "")

        transport = FakeTransport()
        adapter = NodyHubAdapter(ProviderConfig("nodyhub", "https://nodyhub.com", "test", ()), transport)
        payload = {"model": "grok-video-3", "mode": "text", "prompt": "a blue ball", "resolution": "720p", "duration": 30,
                   "aspect_ratio": "16:9", "generate_audio": True, "_nody_operator_testing": True, "images": [], "videos": [], "audios": []}
        adapter.submit("testing", "grok-video-3", payload)
        self.assertEqual(transport.calls[0], ("https://nodyhub.com/v2/videos/generations", {"model": "grok-video-3", "prompt": "a blue ball", "duration": 30, "resolution": "720P", "ratio": "16:9"}))
        adapter.submit("legacy", "grok-video-3", {**payload, "duration": 6})
        self.assertEqual(transport.calls[1], ("https://nodyhub.com/v1/videos", {"model": "grok-video-3", "prompt": "a blue ball", "seconds": "6", "size": "1280x720"}))


if __name__ == "__main__":
    unittest.main()
