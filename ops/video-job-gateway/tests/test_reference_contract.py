import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_contract import (
    ReferenceContractError,
    ReferenceMediaVerifier,
    reference_digest,
    stable_reference_identity,
    validate_reference_payload,
)


def payload() -> dict:
    return {
        "reference_videos": [{
            "role": "reference_video", "url": "https://tos.example.com/a.mp4?sig=one",
            "sha256": "a" * 64, "mime_type": "video/mp4", "size_bytes": 1024,
            "duration_seconds": "4.000000", "width_pixels": 720, "height_pixels": 1280,
        }],
        "reference_audios": [{
            "role": "reference_audio", "url": "https://tos.example.com/a.mp3?sig=one",
            "sha256": "b" * 64, "mime_type": "audio/mpeg", "codec": "mp3", "size_bytes": 512,
            "duration_seconds": "4.000000", "sample_rate_hz": 44100, "channels": 2,
        }],
    }


class ReferenceContractTests(unittest.TestCase):
    def test_accepts_exact_v22_mp4_and_mp3_shape(self):
        normalized = validate_reference_payload(payload())
        self.assertEqual(normalized["reference_audios"][0]["codec"], "mp3")

    def test_accepts_official_wav_reference_shape(self):
        value = payload()
        value["reference_audios"][0]["url"] = "https://tos.example.com/a.wav"
        value["reference_audios"][0]["mime_type"] = "audio/wav"
        value["reference_audios"][0]["codec"] = "wav"
        self.assertEqual(validate_reference_payload(value)["reference_audios"][0]["codec"], "wav")

    def test_accepts_aac_and_m4a_reference_shapes(self):
        for mime_type, codec, suffix in (
            ("audio/aac", "aac", "aac"),
            ("audio/mp4", "m4a", "m4a"),
        ):
            value = payload()
            value["reference_audios"][0]["url"] = f"https://tos.example.com/a.{suffix}"
            value["reference_audios"][0]["mime_type"] = mime_type
            value["reference_audios"][0]["codec"] = codec
            self.assertEqual(validate_reference_payload(value)["reference_audios"][0]["codec"], codec)

    def test_rejects_audio_without_image_or_video_reference(self):
        value = payload()
        value["reference_videos"] = []
        with self.assertRaises(ReferenceContractError) as failure:
            validate_reference_payload(value)
        self.assertEqual(failure.exception.code, "reference_audio_only_unsupported")

        value["images"] = [{"url": "https://tos.example.com/frame.png"}]
        self.assertEqual(validate_reference_payload(value)["reference_audios"][0]["codec"], "mp3")

    def test_rotated_signed_urls_do_not_change_identity_or_digest(self):
        first = payload()
        second = payload()
        second["reference_videos"][0]["url"] = "https://tos.example.com/a.mp4?sig=two"
        second["reference_audios"][0]["url"] = "https://tos.example.com/a.mp3?sig=two"
        self.assertEqual(stable_reference_identity(first), stable_reference_identity(second))
        self.assertEqual(reference_digest(first), reference_digest(second))
        self.assertNotIn("url", str(stable_reference_identity(first)))

    def test_rejects_private_url_non_mp3_and_non_six_decimal_duration(self):
        cases = []
        private = payload(); private["reference_audios"][0]["url"] = "https://127.0.0.1/a.mp3"; cases.append(private)
        mismatched = payload(); mismatched["reference_audios"][0]["codec"] = "wav"; cases.append(mismatched)
        float_duration = payload(); float_duration["reference_videos"][0]["duration_seconds"] = 4.0; cases.append(float_duration)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ReferenceContractError):
                validate_reference_payload(value)

    @mock.patch("reference_contract.socket.getaddrinfo")
    def test_image_origin_requires_exact_allowlisted_public_host(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("8.8.8.8", 443))]
        verifier = ReferenceMediaVerifier(("tos.example.com",))
        verifier.verify_image_origins(["https://tos.example.com/frame.png?signature=one"])

        with self.assertRaises(ReferenceContractError) as subdomain:
            verifier.verify_image_origins(["https://evil.tos.example.com/frame.png"])
        self.assertEqual(subdomain.exception.code, "video_image_url_invalid")

    @mock.patch("reference_contract.socket.getaddrinfo")
    def test_image_origin_rejects_private_dns_resolution(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        verifier = ReferenceMediaVerifier(("tos.example.com",))
        with self.assertRaises(ReferenceContractError) as failure:
            verifier.verify_image_origins(["https://tos.example.com/frame.png"])
        self.assertEqual(failure.exception.code, "video_image_url_invalid")


if __name__ == "__main__":
    unittest.main()
