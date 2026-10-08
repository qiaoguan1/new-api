"""Exact authenticated cost evidence gates every new Nody media tuple."""

import copy
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog import Catalog
from nody_media_contracts import NodyMediaContracts


def profile(**changes):
    row = {
        "model": "wan3.0-video", "mode": "all_reference", "image_count": 1,
        "video_count": 1, "audio_count": 1, "resolution": "480p", "duration": 2,
        "generate_audio": True, "actual_cost_cny_exact": "0.500000",
        "input_video_seconds_exact": "5.000000",
        "input_audio_seconds_exact": "2.000000",
        "evidence_task_id": "f38d6547-27af-4f3b-8945-8c643162c432",
        "evidence_source": "nodyhub_authenticated_video_task", "status": "succeeded", "cost_status": "actual",
    }
    row.update(changes)
    if row["video_count"] == 0:
        row.pop("input_video_seconds_exact", None)
    if row["audio_count"] == 0 and "input_audio_seconds_exact" not in changes:
        row.pop("input_audio_seconds_exact", None)
    return row


def quote(contracts, row, **changes):
    arguments = {field: row[field] for field in (
        "model", "resolution", "duration", "mode", "image_count", "video_count", "audio_count", "generate_audio",
    )}
    if "input_video_seconds_exact" in row:
        arguments["input_video_seconds_exact"] = row["input_video_seconds_exact"]
    if "input_audio_seconds_exact" in row:
        arguments["input_audio_seconds_exact"] = row["input_audio_seconds_exact"]
    if "aspect_ratio" in row:
        arguments["aspect_ratio"] = row["aspect_ratio"]
    arguments.update(changes)
    return contracts.quote(**arguments)


class NodyMediaContractTests(unittest.TestCase):
    def load(self, rows, **changes):
        raw = {"schema_version": "xtai-nody-media-input-v1", "revision": "fixture-media-evidence", "profiles": rows}
        raw.update(changes)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "media-contracts.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return NodyMediaContracts.load(path)

    def test_exact_media_tuple_quote_has_private_bill_identity_and_times_1_5_price(self):
        row = profile()
        contracts = self.load([row])
        self.assertEqual(quote(contracts, row), {
            "model": "wan3.0-video", "resolution": "480p", "operation_mode": "all_reference",
            "image_count": 1, "video_count": 1, "audio_count": 1, "generate_audio": True,
            "aspect_ratio": "16:9",
            "duration": 2, "currency": "CNY", "billing_unit": "output_second",
            "cny_per_second_exact": "0.375000", "amount_cny_exact": "0.750000", "output_seconds": 2,
            "reference_cost_cny_exact": "0.500000", "fallback_multiplier_exact": "1.5",
            "pricing_revision": "fixture-media-evidence", "price_source": "verified_upstream_1_5",
            "contract_version": "xtai-video-pricing-v1", "input_rate_class": "with_video_input", "fallback": False,
            "input_video_seconds_exact": "5.000000",
            "input_audio_seconds_exact": "2.000000",
            "media_contract_evidence": {"task_id": row["evidence_task_id"], "source": row["evidence_source"]},
        })
        public = contracts.public_rows()
        self.assertEqual(public[0]["amount_cny_exact"], "0.750000")
        self.assertEqual(public[0]["input_video_seconds_exact"], "5.000000")
        for private_field in ("reference_cost_cny_exact", "actual_cost_cny_exact", "media_contract_evidence", "evidence_task_id", "evidence_source"):
            self.assertNotIn(private_field, public[0])

    def test_no_tuple_extrapolation_or_boolean_integer_coercion(self):
        row = profile()
        contracts = self.load([row])
        changes = [
            {"model": "wan3.0-video-prime"}, {"resolution": "720p"}, {"duration": 3},
            {"mode": "reference"}, {"image_count": 2}, {"video_count": 0}, {"audio_count": 2},
            {"generate_audio": False}, {"duration": True}, {"image_count": True}, {"video_count": True},
            {"audio_count": True}, {"generate_audio": 1}, {"input_video_seconds_exact": "6.000000"},
            {"input_video_seconds_exact": None}, {"input_video_seconds_exact": 5},
            {"aspect_ratio": "9:16"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                quote(contracts, row, **change)

    def test_media_profile_requires_successful_actual_authenticated_bill(self):
        changes = [
            {"status": "running"}, {"cost_status": "estimated"}, {"evidence_source": "manual_estimate"},
            {"evidence_task_id": ""}, {"evidence_task_id": "not-a-task"}, {"actual_cost_cny_exact": "NaN"},
            {"actual_cost_cny_exact": "-0.500000"}, {"actual_cost_cny_exact": "0.000000"},
            {"actual_cost_cny_exact": "100.000001"}, {"actual_cost_cny_exact": "0.5"},
            {"actual_cost_cny_exact": "0.5000000"}, {"actual_cost_cny_exact": 0.5},
            {"image_count": -1}, {"video_count": True}, {"audio_count": 6}, {"generate_audio": "true"},
            {"duration": "2"}, {"model": "seedance-2.0"}, {"mode": "text"}, {"mode": "arbitrary"},
            {"mode": {}}, {"model": []}, {"image_count": None}, {"resolution": []},
            {"resolution": "4k"}, {"input_video_seconds_exact": "0.000000"},
            {"input_video_seconds_exact": "-5.000000"}, {"input_video_seconds_exact": "NaN"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([profile(**change)])

    def test_every_profile_is_checked_against_exact_supported_model_mode_wire(self):
        unsupported = [
            profile(model="omni-flash", mode="all_reference", duration=4, resolution="720p", image_count=2, audio_count=0),
            profile(model="grok-video-3", duration=6, resolution="720p", image_count=1, audio_count=0),
            profile(model="flux-3-video", duration=5, resolution="720p", image_count=1, audio_count=0),
            profile(mode="first_frame", image_count=2, video_count=0, audio_count=0),
            profile(mode="last_frame", image_count=1, video_count=1, audio_count=0),
            profile(mode="reference", image_count=1, video_count=0, audio_count=1),
        ]
        for row in unsupported:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.load([row])

    def test_input_video_and_audio_durations_are_required_and_exact(self):
        missing_video = profile()
        missing_video.pop("input_video_seconds_exact")
        with self.assertRaises(ValueError):
            self.load([missing_video])
        missing_audio = profile()
        missing_audio.pop("input_audio_seconds_exact")
        with self.assertRaises(ValueError):
            self.load([missing_audio])
        row = profile(input_audio_seconds_exact="3.000000")
        contracts = self.load([row])
        self.assertEqual(quote(contracts, row)["input_audio_seconds_exact"], "3.000000")
        self.assertEqual(contracts.public_rows()[0]["input_audio_seconds_exact"], "3.000000")
        for value in (None, "4.000000", "NaN", 3, "0.000000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quote(contracts, row, input_audio_seconds_exact=value)
        for value in (None, "NaN", "0.000000", "3.0000000", 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load([profile(input_audio_seconds_exact=value)])

    def test_profile_aspect_ratio_is_exact_and_absent_means_only_16_9(self):
        row = profile(aspect_ratio="1:1")
        contracts = self.load([row])
        self.assertEqual(quote(contracts, row)["aspect_ratio"], "1:1")
        with self.assertRaises(ValueError):
            quote(contracts, row, aspect_ratio="16:9")
        for value in (None, "7:5", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load([profile(aspect_ratio=value)])

    def test_one_actual_task_cannot_evidence_two_different_tuples(self):
        with self.assertRaises(ValueError):
            self.load([profile(), profile(duration=3)])

    def test_audio_and_video_duration_bindings_cannot_exist_without_corresponding_material(self):
        for row in [
            {**profile(video_count=0), "input_video_seconds_exact": "5.000000"},
            profile(audio_count=0, input_audio_seconds_exact="3.000000"),
        ]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.load([row])

    def test_input_durations_are_bounded_by_actual_material_count(self):
        changes = [
            {"input_video_seconds_exact": "16.000000"},
            {"input_video_seconds_exact": "0.999999"},
            {"video_count": 2, "input_video_seconds_exact": "1.999999"},
            {"video_count": 2, "input_video_seconds_exact": "30.000001"},
            {"audio_count": 2, "input_audio_seconds_exact": "1.999999"},
            {"audio_count": 1, "input_audio_seconds_exact": "15.000001"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([profile(**change)])
        row = profile(video_count=5, audio_count=5, input_video_seconds_exact="75.000000", input_audio_seconds_exact="75.000000")
        contracts = self.load([row])
        self.assertEqual(quote(contracts, row)["input_video_seconds_exact"], "75.000000")
        with self.assertRaises(ValueError):
            quote(contracts, row, input_video_seconds_exact="75.000001")

    def test_decimal_rounding_is_exact_and_profiles_are_copied(self):
        row = profile(mode="first_frame", video_count=0, audio_count=0, actual_cost_cny_exact="0.000001")
        contracts = self.load([row])
        row["actual_cost_cny_exact"] = "99.000000"
        result = quote(contracts, row)
        self.assertEqual(result["amount_cny_exact"], "0.000002")
        self.assertEqual(result["reference_cost_cny_exact"], "0.000001")
        self.assertEqual(result["input_rate_class"], "without_video_input")

    def test_missing_config_leaves_catalog_and_legacy_text_unchanged(self):
        contracts = NodyMediaContracts.load(None)
        base = Catalog.load(ROOT / "catalog.json")
        result = contracts.catalog(base)
        self.assertEqual(result.models, base.models)
        self.assertEqual(result.revision, base.revision)
        self.assertEqual(contracts.public_rows(), [])
        with self.assertRaises(ValueError):
            quote(contracts, profile())

    def test_catalog_adds_only_evidenced_discrete_specs_and_resolution_local_routes(self):
        rows = [
            profile(),
            profile(mode="first_last_frame", image_count=2, video_count=0, audio_count=0,
                    resolution="720p", duration=6, generate_audio=False,
                    evidence_task_id="d4c04b41-987d-42cd-bf8e-ac89b66060d0"),
        ]
        contracts = self.load(rows)
        base = Catalog.load(ROOT / "catalog.json")
        before = copy.deepcopy(base.models)
        result = contracts.catalog(base)
        old, new = base.model("wan3.0-video"), result.model("wan3.0-video")
        self.assertEqual(new.operation_modes, ("text", "all_reference", "first_last_frame"))
        self.assertEqual(new.resolutions, ("480p", "720p"))
        self.assertEqual(new.durations, (2, 6))
        self.assertEqual((new.duration_min, new.duration_max), (2, 6))
        self.assertEqual((new.max_images, new.max_videos), (2, 1))
        routes = {route.resolution: route for route in new.routes}
        self.assertTrue(routes["480p"].supports_reference_video)
        self.assertTrue(routes["480p"].supports_reference_audio)
        self.assertEqual(routes["480p"].max_reference_audios, 1)
        self.assertEqual(routes["480p"].max_total_assets, 3)
        self.assertFalse(routes["720p"].supports_reference_video)
        self.assertFalse(routes["720p"].supports_reference_audio)
        self.assertEqual(routes["720p"].max_reference_audios, 0)
        self.assertEqual(routes["720p"].max_total_assets, 2)
        self.assertEqual(old.operation_modes, ("text",))
        self.assertEqual(base.models, before)
        self.assertEqual(result.revision, base.revision + "+fixture-media-evidence")
        for item in base.models:
            if item.id != "wan3.0-video":
                self.assertIs(result.model(item.id), item)

    def test_duplicate_tuples_invalid_schema_revision_and_profile_limits_fail_closed(self):
        row = profile()
        with self.assertRaises(ValueError):
            self.load([row, row])
        for change in [
            {"schema_version": "xtai-nody-image-input-v1"}, {"revision": ""},
            {"revision": "x" * 121}, {"profiles": {}}, {"profiles": [row] * 81},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([row], **change)

    def test_failed_profile_does_not_mutate_input_and_no_provider_request_is_possible(self):
        row = profile()
        before = copy.deepcopy(row)
        contracts = self.load([row])
        quote(contracts, row)
        contracts.public_rows()
        contracts.catalog(Catalog.load(ROOT / "catalog.json"))
        self.assertEqual(row, before)


if __name__ == "__main__":
    unittest.main()
