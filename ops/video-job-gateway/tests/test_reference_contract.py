import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_contract import ReferenceContractError, reference_digest, stable_reference_identity, validate_reference_payload


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


if __name__ == "__main__":
    unittest.main()
