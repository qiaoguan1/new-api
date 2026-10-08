"""Nody media admission must price and verify before durable task freeze."""

import copy
import json
import pathlib
import sys
import tempfile
import unittest
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapters import ProviderConfig
from app import Config, Gateway, GatewayError
from nodyhub import NodyHubAdapter
from nodyhub import verified_quote
from reference_contract import ReferenceContractError
from store import _reservation_from_payload


def profile(**changes):
    row = {"model": "wan3.0-video", "mode": "reference", "resolution": "480p", "duration": 2,
           "image_count": 0, "video_count": 1, "audio_count": 0, "generate_audio": True,
           "aspect_ratio": "16:9", "input_video_seconds_exact": "1.000000",
           "actual_cost_cny_exact": "0.500000", "status": "succeeded", "cost_status": "actual",
           "evidence_task_id": "f38d6547-27af-4f3b-8945-8c643162c432", "evidence_source": "nodyhub_authenticated_video_task"}
    row.update(changes)
    if not row["video_count"]:
        row.pop("input_video_seconds_exact", None)
    return row


def video():
    return {"url": "https://media.example/video.mp4?token=private", "role": "reference_video", "sha256": "b" * 64,
            "size_bytes": 128, "duration_seconds": "1.000000", "mime_type": "video/mp4", "width_pixels": 640, "height_pixels": 360}


def body(**changes):
    row = {"provider_id": "video-aixingtu-api", "request_id": "nody-media-case", "model": "wan3.0-video",
           "prompt": "a blue ball", "mode": "reference", "resolution": "480p", "duration": 2,
           "aspect_ratio": "16:9", "generate_audio": True, "reference_videos": [video()], "reference_audios": []}
    row.update(changes)
    return row


@contextmanager
def media_test_directory():
    """Prevent constructor recovery from starting any real upstream submission."""
    with tempfile.TemporaryDirectory() as directory, patch.object(Gateway, "start_submit"):
        yield directory


class NodyMediaGatewayTests(unittest.TestCase):
    def gateway(self, directory, rows=None, image_rows=None):
        path = pathlib.Path(directory) / "media-contracts.json"
        path.write_text(json.dumps({"schema_version": "xtai-nody-media-input-v1", "revision": "media-fixture", "profiles": rows or [profile()]}), encoding="utf-8")
        image_path = None
        if image_rows:
            image_path = pathlib.Path(directory) / "image-contracts.json"
            image_path.write_text(json.dumps({"schema_version": "xtai-nody-image-input-v1", "revision": "image-fixture", "profiles": image_rows}), encoding="utf-8")
        provider = ProviderConfig("nodyhub", "https://nodyhub.com", "test", ("getapib.org",))
        config = Config(token="test", data_dir=pathlib.Path(directory) / "state", catalog_file=ROOT / "catalog.json",
                        providers={"nodyhub": provider}, pricing_file=ROOT / "relay-pricing.json", public_base_url="https://api.aixingtuyun.com",
                        v21_approved_providers=frozenset({"nodyhub"}), reference_media_hosts=("media.example",),
                        nody_image_contract_file=image_path, nody_media_contract_file=path)
        verifier = SimpleNamespace(verify_images=Mock(), verify=Mock())
        gateway = Gateway(config, adapters={"nodyhub": NodyHubAdapter(provider)}, billing_collectors={"nodyhub": SimpleNamespace(ready=True)},
                          reference_verifier=verifier, start_monitor=False)
        gateway.start_submit = Mock()
        return gateway, verifier

    def test_preflight_prices_and_verifies_without_freezing_or_private_metadata_leak(self):
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory)
            result = gateway.preflight_nody_media(body())
            self.assertEqual(result["reserved_cny_exact"], "0.750000")
            self.assertEqual(result["input_video_seconds_exact"], "1.000000")
            self.assertEqual((result["image_count"], result["video_count"], result["audio_count"]), (0, 1, 0))
            self.assertEqual(result["operation_mode"], "reference")
            self.assertEqual(result["aspect_ratio"], "16:9")
            self.assertFalse(result["task_created"])
            self.assertIsNone(gateway.store.get(request_id="nody-media-case"))
            gateway.start_submit.assert_not_called()
            verifier.verify.assert_called_once()
            for private in ("token=private", "reference_cost", "sha256", "media_contract_evidence", "f38d6547"):
                self.assertNotIn(private, json.dumps(result))

    def test_exact_input_duration_and_content_verification_fail_before_freeze(self):
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory)
            changed = body(reference_videos=[{**video(), "duration_seconds": "2.000000"}])
            with self.assertRaises(GatewayError):
                gateway.submit_v22(changed, idempotency_key=changed["request_id"])
            verifier.verify.assert_not_called()
            self.assertIsNone(gateway.store.get(request_id=changed["request_id"]))
            verifier.verify.side_effect = ReferenceContractError("reference_video_identity_mismatch", "bad digest")
            with self.assertRaises(GatewayError) as failure:
                gateway.submit_v22(body(), idempotency_key="nody-media-case")
            self.assertEqual(failure.exception.code, "reference_video_identity_mismatch")
            self.assertIsNone(gateway.store.get(request_id="nody-media-case"))
            gateway.start_submit.assert_not_called()

    def test_media_reservation_frozen_replay_survives_removed_profiles_and_unsafe_state(self):
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory)
            first, reused = gateway.submit_v22(body(), idempotency_key="nody-media-case")
            self.assertFalse(reused)
            self.assertEqual(first["billing"]["reserved_amount"], "0.750000")
            internal = gateway.store.get(job_id=first["job_id"], internal=True)
            frozen = json.loads(internal["payload_json"])
            self.assertEqual(frozen["videos"][0]["role"], "reference")
            self.assertNotIn("url", frozen["reference_input"]["reference_videos"][0])
            restarted = Gateway(replace(gateway.config, nody_media_contract_file=None), adapters=gateway.adapters,
                                billing_collectors={"nodyhub": SimpleNamespace(ready=False)}, reference_verifier=verifier, start_monitor=False)
            restarted.start_submit = Mock()
            rotated = body(reference_videos=[{**video(), "url": "https://media.example/video.mp4?token=rotated"}])
            second, reused = restarted.submit_v22(rotated, idempotency_key="nody-media-case")
            self.assertTrue(reused)
            self.assertEqual(second["job_id"], first["job_id"])
            restarted.start_submit.assert_not_called()
            verifier.verify.assert_called_once()
            for changes in ({"sha256": "c" * 64}, {"duration_seconds": "2.000000"}):
                with self.subTest(changes=changes), self.assertRaises(GatewayError) as failure:
                    restarted.submit_v22(body(reference_videos=[{**video(), **changes}]), idempotency_key="nody-media-case")
                self.assertEqual(failure.exception.status, 409)

    def test_uncertain_accepted_media_job_never_resubmits_or_reverifies_on_replay(self):
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory)
            first, _ = gateway.submit_v22(body(), idempotency_key="nody-media-case")
            gateway.store.claim_submit(first["job_id"])
            gateway.store.finish(first["job_id"], "uncertain", error={"code": "unknown_submit", "uncertain": True})
            second, reused = gateway.submit_v22(body(), idempotency_key="nody-media-case")
            self.assertTrue(reused)
            self.assertEqual(second["status"], "uncertain")
            self.assertEqual(second["job_id"], first["job_id"])
            gateway.start_submit.assert_called_once()
            verifier.verify.assert_called_once()

    def test_first_last_roles_false_audio_and_audio_only_are_nody_specific(self):
        rows = [profile(mode="first_last_frame", image_count=2, video_count=0, generate_audio=False),
                profile(audio_count=1, video_count=0, input_audio_seconds_exact="1.000000", evidence_task_id="d4c04b41-987d-42cd-bf8e-ac89b66060d0")]
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory, rows)
            raw = body(mode="first_last_frame", reference_videos=[], images=["https://media.example/a.png", "https://media.example/b.png"],
                       image_roles=["first", "last"], image_identities=["a" * 64, "c" * 64], generate_audio=False)
            first, _ = gateway.submit_v22(raw, idempotency_key=raw["request_id"])
            frozen = json.loads(gateway.store.get(job_id=first["job_id"], internal=True)["payload_json"])
            self.assertEqual([item["role"] for item in frozen["images"]], ["first_frame", "last_frame"])
            self.assertIs(frozen["generate_audio"], False)
            verifier.verify_images.assert_called_once_with(frozen["images"], max_images=10, aspect_ratio="16:9", deadline=ANY)
            audio = {"url": "https://media.example/a.mp3", "role": "reference_audio", "sha256": "d" * 64, "size_bytes": 128,
                     "duration_seconds": "1.000000", "mime_type": "audio/mpeg", "codec": "mp3", "sample_rate_hz": 44100, "channels": 1}
            raw = body(request_id="nody-audio-only", reference_videos=[], reference_audios=[audio])
            self.assertEqual(gateway.preflight_nody_media(raw)["input_audio_seconds_exact"], "1.000000")

    def test_mixed_image_and_av_verification_share_one_60_second_deadline(self):
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory, [profile(mode="all_reference", image_count=1)])
            raw = body(mode="all_reference", images=["https://media.example/a.png"], image_roles=["reference"], image_identities=["a" * 64])
            with patch("app.time.monotonic", return_value=100.0):
                gateway.preflight_nody_media(raw)
            self.assertEqual(verifier.verify_images.call_args.kwargs["deadline"], 160.0)
            self.assertEqual(verifier.verify.call_args.kwargs["deadline"], 160.0)
            self.assertEqual(str(verifier.verify.call_args.kwargs["duration_tolerance"]), "0.000001")

    def test_media_catalog_caps_prices_and_no_config_leave_legacy_text_unchanged(self):
        with media_test_directory() as directory:
            gateway, _ = self.gateway(directory)
            row = next(item for item in gateway.capabilities("xtai-video-billing-v2.2")["capabilities"]["video"]["models"] if item["id"] == "wan3.0-video")
            self.assertTrue(row["reference_video"]["supported"])
            self.assertFalse(row["reference_audio"]["supported"])
            self.assertEqual(row["reference_video"]["max_count"], 1)
            public = gateway.video_prices()
            self.assertEqual(public["media_reference_pricing"]["models"][0]["amount_cny_exact"], "0.750000")
            self.assertNotIn("media_contract_evidence", json.dumps(public))
            empty = Gateway(replace(gateway.config, nody_media_contract_file=None), adapters=gateway.adapters,
                            billing_collectors={"nodyhub": SimpleNamespace(ready=True)}, start_monitor=False)
            empty.start_submit = Mock()
            raw = body(mode="text", reference_videos=[], reference_audios=[])
            raw.pop("mode")
            first, _ = empty.submit_v22(raw, idempotency_key=raw["request_id"])
            self.assertEqual(first["billing"]["reserved_amount"], "1.125000")
            with self.assertRaises(GatewayError):
                empty.preflight_nody_media(body(request_id="no-media-profile"))

    def test_media_caps_publish_safe_metadata_and_only_evidenced_combinations(self):
        rows = [profile(mode="all_reference", image_count=1, audio_count=1, input_audio_seconds_exact="1.000000"),
                profile(video_count=0, audio_count=1, input_audio_seconds_exact="1.000000",
                        evidence_task_id="d4c04b41-987d-42cd-bf8e-ac89b66060d0")]
        with media_test_directory() as directory:
            gateway, _ = self.gateway(directory, rows)
            row = next(item for item in gateway.capabilities("xtai-video-billing-v2.2")["capabilities"]["video"]["models"] if item["id"] == "wan3.0-video")
            video_cap, audio_cap = row["reference_video"], row["reference_audio"]
            self.assertEqual(video_cap["roles"], ["reference_video"])
            self.assertEqual(video_cap["mime_types"], ["video/mp4"])
            self.assertEqual(video_cap["video_codecs"], ["h264", "hevc"])
            self.assertEqual(video_cap["max_video_bytes"], 200 * 1024 * 1024)
            self.assertEqual((video_cap["min_duration_seconds"], video_cap["max_duration_seconds"]), (1, 15))
            self.assertTrue(video_cap["supports_images_with_video"])
            self.assertTrue(video_cap["supports_audio_with_video"])
            self.assertTrue(video_cap["supports_generate_audio_with_video"])
            self.assertEqual(video_cap["max_total_assets"], 3)
            self.assertEqual(audio_cap["roles"], ["reference_audio"])
            self.assertEqual(audio_cap["audio_codecs"], ["mp3", "wav", "aac", "m4a"])
            self.assertEqual(audio_cap["max_audio_bytes"], 15 * 1024 * 1024)
            self.assertFalse(audio_cap["requires_non_audio_input"])
            self.assertTrue(audio_cap["supports_images_with_audio"])
            self.assertTrue(audio_cap["supports_video_with_audio"])
            self.assertEqual(audio_cap["max_total_assets"], 3)
            self.assertTrue(row["reference_video_audio"]["available"])
            self.assertEqual(row["reference_video_audio"]["max_total_assets"], 3)

    def test_media_caps_never_advertise_absent_combinations_or_audio_only_support(self):
        with media_test_directory() as directory:
            gateway, _ = self.gateway(directory, [profile()])
            row = next(item for item in gateway.capabilities("xtai-video-billing-v2.2")["capabilities"]["video"]["models"] if item["id"] == "wan3.0-video")
            self.assertFalse(row["reference_video"]["supports_images_with_video"])
            self.assertFalse(row["reference_video"]["supports_audio_with_video"])
            self.assertEqual(row["reference_video"]["max_total_assets"], 1)
            self.assertFalse(row["reference_audio"]["supported"])
            self.assertFalse(row["reference_audio"]["available"])
            self.assertTrue(row["reference_audio"]["requires_non_audio_input"])
            self.assertFalse(row["reference_audio"]["supports_images_with_audio"])
            self.assertFalse(row["reference_audio"]["supports_video_with_audio"])
            empty = Gateway(replace(gateway.config, nody_media_contract_file=None), adapters=gateway.adapters,
                            billing_collectors={"nodyhub": SimpleNamespace(ready=True)}, start_monitor=False)
            row = next(item for item in empty.capabilities("xtai-video-billing-v2.2")["capabilities"]["video"]["models"] if item["id"] == "wan3.0-video")
            self.assertFalse(row["reference_video"]["available"])
            self.assertFalse(row["reference_audio"]["available"])
            self.assertFalse(row["reference_video_audio"]["available"])
            self.assertEqual(row["reference_video"]["max_count"], 0)
            self.assertEqual(row["reference_video"]["max_total_assets"], 0)

    def test_new_media_overlay_preserves_existing_grok_image_quote_and_fingerprint_path(self):
        image_row = {"model": "grok-imagine-video-official", "mode": "reference", "image_count": 1, "resolution": "480p", "duration": 1,
                     "actual_cost_cny_exact": "0.300000", "evidence_task_id": "f38d6547-27af-4f3b-8945-8c643162c432",
                     "evidence_source": "nodyhub_authenticated_video_task", "status": "succeeded", "cost_status": "actual"}
        with media_test_directory() as directory:
            gateway, verifier = self.gateway(directory, image_rows=[image_row])
            raw = body(model="grok-imagine-video-official", duration=1, images=["https://media.example/grok.png"],
                       image_roles=["reference"], image_identities=["a" * 64])
            raw.pop("reference_videos")
            raw.pop("reference_audios")
            snapshot, _ = gateway.submit_v22(raw, idempotency_key=raw["request_id"])
            self.assertEqual(snapshot["billing"]["reserved_amount"], "0.450000")
            frozen = json.loads(gateway.store.get(job_id=snapshot["job_id"], internal=True)["payload_json"])
            self.assertNotIn("_nody_media_contract", frozen)
            self.assertIn("image_contract_evidence", frozen["_relay_price"])
            verifier.verify_images.assert_called_once_with(frozen["images"])
            verifier.verify.assert_not_called()

    def test_reservation_rejects_forged_media_metadata_without_changing_valid_quote(self):
        with media_test_directory() as directory:
            gateway, _ = self.gateway(directory)
            snapshot, _ = gateway.submit_v22(body(), idempotency_key="nody-media-case")
            payload = json.loads(gateway.store.get(job_id=snapshot["job_id"], internal=True)["payload_json"])
            self.assertEqual(_reservation_from_payload(json.dumps(payload))["status"], "reserved")
            for changes in ({"video_count": 2}, {"input_video_seconds_exact": "2.000000"}, {"aspect_ratio": "1:1"}, {"output_seconds": 3}, {"input_rate_class": "without_video_input"}):
                altered = copy.deepcopy(payload)
                altered["_relay_price"].update(changes)
                with self.subTest(changes=changes):
                    self.assertNotEqual(_reservation_from_payload(json.dumps(altered))["status"], "reserved")
            altered = copy.deepcopy(payload)
            altered["_relay_price"] = verified_quote("wan3.0-video", "480p", 2)
            self.assertNotEqual(_reservation_from_payload(json.dumps(altered))["status"], "reserved")
            for seconds in ("NaN", "Infinity", "0.000000", "16.000000", "1"):
                altered = copy.deepcopy(payload)
                altered["reference_input"]["reference_videos"][0]["duration_seconds"] = seconds
                altered["_relay_price"]["input_video_seconds_exact"] = seconds
                altered["input_video_seconds_exact"] = seconds
                with self.subTest(seconds=seconds):
                    self.assertNotEqual(_reservation_from_payload(json.dumps(altered))["status"], "reserved")


if __name__ == "__main__":
    unittest.main()
