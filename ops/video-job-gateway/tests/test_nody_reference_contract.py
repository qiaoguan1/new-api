"""Nody AV admission expands model-local limits without changing Seedance."""

import copy
import hashlib
import io
import json
import pathlib
import sys
import unittest
from contextlib import contextmanager
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import reference_contract
from reference_contract import ReferenceContractError, ReferenceMediaVerifier, validate_reference_payload


def video(**changes):
    row = {"role": "reference_video", "url": "https://media.example/video.mp4", "sha256": "a" * 64,
           "mime_type": "video/mp4", "size_bytes": 1024, "duration_seconds": "1.000000",
           "width_pixels": 640, "height_pixels": 360}
    return {**row, **changes}


def audio(**changes):
    row = {"role": "reference_audio", "url": "https://media.example/audio.mp3", "sha256": "b" * 64,
           "mime_type": "audio/mpeg", "codec": "mp3", "size_bytes": 512, "duration_seconds": "1.000000",
           "sample_rate_hz": 44100, "channels": 2}
    return {**row, **changes}


class NodyReferenceContractTests(unittest.TestCase):
    def validate(self, raw, model="wan3.0-video"):
        validator = getattr(reference_contract, "validate_nody_reference_payload", None)
        self.assertIsNotNone(validator, "model-local Nody reference validation must exist")
        return validator(raw, model)

    def test_wan_accepts_five_video_and_audio_items_including_one_second(self):
        raw = {"mode": "all_reference", "reference_videos": [video() for _ in range(5)],
               "reference_audios": [audio() for _ in range(5)]}
        before = copy.deepcopy(raw)
        for model in ("wan3.0-video", "wan3.0-video-prime"):
            result = self.validate(raw, model)
            self.assertEqual(result["reference_videos"], raw["reference_videos"])
            self.assertEqual(result["reference_audios"], raw["reference_audios"])
        self.assertEqual(raw, before)
        for field, item in (("reference_videos", video()), ("reference_audios", audio())):
            with self.subTest(field=field), self.assertRaises(ReferenceContractError) as caught:
                self.validate({"mode": "all_reference", field: [item for _ in range(6)]})
            self.assertEqual(caught.exception.code, "reference_video_count_invalid" if field == "reference_videos" else "reference_audio_count_invalid")

    def test_wan_audio_only_and_seventy_five_seconds_per_kind_are_model_local(self):
        self.assertEqual(self.validate({"mode": "reference", "reference_audios": [audio()]})["reference_audios"], [audio()])
        raw = {"mode": "all_reference", "reference_videos": [video(duration_seconds="15.000000") for _ in range(5)],
               "reference_audios": [audio(duration_seconds="15.000000") for _ in range(5)]}
        self.assertEqual(len(self.validate(raw)["reference_videos"]), 5)

    def test_omni_accepts_only_one_video_and_no_audio(self):
        self.assertEqual(self.validate({"mode": "reference", "reference_videos": [video()]}, "omni-flash")["reference_videos"], [video()])
        for raw in ({"reference_videos": [video(), video()]}, {"reference_audios": [audio()]}):
            with self.subTest(raw=raw), self.assertRaises(ReferenceContractError):
                self.validate(raw, "omni-flash")

    def test_empty_av_is_valid_for_every_nody_model_but_frames_cannot_take_av(self):
        for model in ("wan3.0-video", "wan3.0-video-prime", "omni-flash", "flux-3-video", "grok-video-3", "grok-imagine-1.5-video", "grok-imagine-video-official"):
            self.assertEqual(self.validate({"mode": "first_frame"}, model), {"reference_videos": [], "reference_audios": []})
        for mode in ("first_frame", "last_frame", "first_last_frame", "text"):
            for field, item in (("reference_videos", video()), ("reference_audios", audio())):
                with self.subTest(mode=mode, field=field), self.assertRaises(ReferenceContractError) as caught:
                    self.validate({"mode": mode, field: [item]})
                self.assertEqual(caught.exception.code, "reference_input_combination_unsupported")

    def test_other_models_and_model_mismatches_cannot_admit_av(self):
        for model in ("flux-3-video", "grok-video-3", "grok-imagine-1.5-video", "grok-imagine-video-official", "seedance-2.0", None, []):
            with self.subTest(model=model), self.assertRaises(ReferenceContractError):
                self.validate({"reference_videos": [video()]}, model)
        with self.assertRaises(ReferenceContractError):
            self.validate({"model": "omni-flash", "reference_videos": [video()]}, "wan3.0-video")

    def test_malformed_modes_return_controlled_reference_errors(self):
        for mode in ({}, [], True, 1, None):
            with self.subTest(mode=mode), self.assertRaises(ReferenceContractError) as caught:
                self.validate({"mode": mode, "reference_videos": [video()]})
            self.assertEqual(caught.exception.code, "reference_input_combination_unsupported")

    def test_av_metadata_types_roles_formats_and_one_to_fifteen_second_bounds_are_strict(self):
        video_changes = [{"duration_seconds": value} for value in ("0.999999", "15.000001", "1", "1.0", 1, True, "NaN")]
        video_changes.extend([{"role": "reference"}, {"mime_type": "video/webm"}, {"sha256": "a" * 63},
                              {"size_bytes": True}, {"size_bytes": 200 * 1024 * 1024 + 1}, {"width_pixels": 0}, {"height_pixels": True}])
        audio_changes = [{"duration_seconds": "0.999999"}, {"role": "reference"}, {"mime_type": "audio/wav"},
                         {"codec": "ogg"}, {"size_bytes": 15 * 1024 * 1024 + 1}, {"sample_rate_hz": 0}, {"channels": True}]
        for field, factory, changes in (("reference_videos", video, video_changes), ("reference_audios", audio, audio_changes)):
            for change in changes:
                with self.subTest(field=field, change=change), self.assertRaises(ReferenceContractError):
                    self.validate({field: [factory(**change)]})
        for raw in ([], {"reference_videos": {}}, {"reference_audios": ["https://media.example/a.mp3"]}):
            with self.subTest(raw=raw), self.assertRaises(ReferenceContractError):
                self.validate(raw)

    def test_urls_reject_unsafe_syntax_hosts_private_literals_and_non443_ports_without_io(self):
        urls = ("http://media.example/a.mp4", "https://media.example:8443/a.mp4", "https://user:pass@media.example/a.mp4",
                "https://@media.example/a.mp4", "https://127.0.0.1/a.mp4", "https://[::1]/a.mp4", "https://[not-ip]/a.mp4",
                "https://media.local/a.mp4", "https://media.example\\a.mp4", "https://media.example/a.mp4#fragment",
                "https://media.example\n/a.mp4", "https://224.0.0.1/a.mp4")
        with patch("reference_contract.socket.getaddrinfo") as dns:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ReferenceContractError) as caught:
                    self.validate({"reference_videos": [video(url=url)]})
                self.assertEqual(caught.exception.code, "reference_video_url_invalid")
        dns.assert_not_called()

    def test_seedance_one_second_audio_only_count_and_total_limits_are_unchanged(self):
        raws = ({"reference_videos": [video()]}, {"reference_audios": [audio(duration_seconds="2.000000")]},
                {"reference_videos": [video(duration_seconds="2.000000") for _ in range(4)]},
                {"reference_videos": [video(duration_seconds="8.000000"), video(duration_seconds="8.000000")]})
        for raw in raws:
            with self.subTest(raw=raw), self.assertRaises(ReferenceContractError):
                validate_reference_payload(raw)

    def test_nody_exact_duration_is_verified_without_relaxing_seedance_default(self):
        from decimal import Decimal

        data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        references = {"reference_videos": [video(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), duration_seconds="5.000000")]}
        verifier = ReferenceMediaVerifier(("media.example",))
        verifier._public_dns_addresses = lambda *_: ("8.8.8.8",)

        @contextmanager
        def response(*args, **kwargs):
            with io.BytesIO(data) as stream:
                stream.headers = Message()
                stream.headers["Content-Type"] = "video/mp4"
                stream.headers["Content-Length"] = str(len(data))
                yield stream

        verifier._open_pinned = response
        payload = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360}],
                   "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "5.090000"}}
        with patch("reference_contract.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(payload))):
            verifier.verify(references)
            with self.assertRaises(ReferenceContractError) as caught:
                verifier.verify(references, duration_tolerance=Decimal("0.000001"))
            self.assertEqual(caught.exception.code, "reference_video_duration_invalid")
        payload["format"]["duration"] = "5.000001"
        with patch("reference_contract.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(payload))):
            verifier.verify(references, duration_tolerance=Decimal("0.000001"))

    def test_duration_policy_cannot_loosen_existing_tolerance_or_admit_nonfinite_values(self):
        from decimal import Decimal

        verifier = ReferenceMediaVerifier(("media.example",))
        for tolerance in (Decimal("-0.000001"), Decimal("0.100001"), Decimal("NaN"), Decimal("Infinity"), 0.1, True):
            with self.subTest(tolerance=tolerance), patch.object(verifier, "_public_dns_addresses") as dns:
                with self.assertRaises(ReferenceContractError):
                    verifier.verify({"reference_videos": [video()]}, duration_tolerance=tolerance)
            dns.assert_not_called()


class NodyImageVerificationOptionsTests(unittest.TestCase):
    def verifier(self, data):
        verifier = ReferenceMediaVerifier(("media.example",))
        verifier._public_dns_addresses = lambda *_: ("8.8.8.8",)

        @contextmanager
        def response(*args, **kwargs):
            with io.BytesIO(data) as stream:
                stream.headers = Message()
                stream.headers["Content-Type"] = "image/png"
                stream.headers["Content-Length"] = str(len(data))
                yield stream

        verifier._open_pinned = response
        return verifier

    def test_profile_local_maximum_ten_images_does_not_change_default_seven(self):
        data = b"\x89PNG\r\n\x1a\nfixture"
        images = [{"url": "https://media.example/a.png", "identity": hashlib.sha256(data).hexdigest()} for _ in range(10)]
        result = SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 640, "height": 360}]}))
        with patch("reference_contract.subprocess.run", return_value=result):
            self.verifier(data).verify_images(images, max_images=10)
        with self.assertRaises(ReferenceContractError) as caught:
            self.verifier(data).verify_images(images)
        self.assertEqual(caught.exception.code, "video_image_count_invalid")

    def test_profile_aspect_ratio_or_adaptive_is_enforced_without_relaxing_default(self):
        data = b"\x89PNG\r\n\x1a\nfixture"
        images = [{"url": "https://media.example/a.png", "identity": hashlib.sha256(data).hexdigest()}]
        for ratio, width, height in (("1:1", 512, 512), ("9:16", 360, 640), ("adaptive", 512, 300)):
            result = SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": width, "height": height}]}))
            with self.subTest(ratio=ratio), patch("reference_contract.subprocess.run", return_value=result):
                self.verifier(data).verify_images(images, aspect_ratio=ratio)
                with self.assertRaises(ReferenceContractError) as caught:
                    self.verifier(data).verify_images(images)
                self.assertEqual(caught.exception.code, "video_image_aspect_ratio_unsupported")
        with patch("reference_contract.subprocess.run", return_value=result), self.assertRaises(ReferenceContractError):
            self.verifier(data).verify_images(images, aspect_ratio="9:16")

    def test_unbounded_or_invalid_profile_options_fail_before_fetch(self):
        image = {"url": "https://media.example/a.png", "identity": "a" * 64}
        for options in ({"max_images": 0}, {"max_images": 11}, {"max_images": True}, {"max_images": "10"},
                        {"aspect_ratio": "7:5"}, {"aspect_ratio": None}, {"aspect_ratio": []}):
            verifier = ReferenceMediaVerifier(("media.example",))
            with self.subTest(options=options), patch.object(verifier, "_open_pinned") as fetch:
                with self.assertRaises(ReferenceContractError):
                    verifier.verify_images([image], **options)
            fetch.assert_not_called()

    def test_slow_image_read_cannot_bypass_the_total_deadline_or_reach_decoder(self):
        data = b"\x89PNG\r\n\x1a\nfixture"
        image = {"url": "https://media.example/a.png", "identity": hashlib.sha256(data).hexdigest()}
        clock = [0]
        verifier = ReferenceMediaVerifier(("media.example",), timeout_seconds=5)
        verifier._public_dns_addresses = lambda *_: ("8.8.8.8",)
        seen = []

        @contextmanager
        def response(*args, **kwargs):
            with io.BytesIO(data) as stream:
                seen.append(stream)
                stream.headers = Message()
                stream.headers["Content-Type"] = "image/png"
                stream.headers["Content-Length"] = str(len(data))
                original_read = stream.read1

                def single_read(size):
                    clock[0] += 5
                    return original_read(size)

                stream.read1 = single_read
                yield stream

        verifier._open_pinned = response
        with patch("reference_contract.time.monotonic", side_effect=lambda: clock[0]), patch("reference_contract.subprocess.run") as probe:
            with self.assertRaises(ReferenceContractError) as caught:
                verifier.verify_images([image], max_images=10, aspect_ratio="adaptive")
        self.assertEqual(caught.exception.code, "video_image_probe_failed")
        self.assertTrue(seen[0].closed)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
