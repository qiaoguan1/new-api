import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import PaisioAdapter, ProviderConfig, RollDekAdapter, ToonflowAdapter


def config(provider: str) -> ProviderConfig:
    return ProviderConfig(
        provider,
        "https://example.com",
        "secret",
        ("example.com",),
    )


class AudioForwardingTests(unittest.TestCase):
    def test_true_is_preserved_for_every_approved_video_adapter(self):
        payload = {
            "prompt": "带声音的视频",
            "duration": 4,
            "aspect_ratio": "16:9",
            "generate_audio": True,
            "_route": {"resolution": "720p", "send_resolution": True},
        }

        paisio = PaisioAdapter(config("paisio")).request_body("sd2-720p", payload)
        rolldek = RollDekAdapter(config("rolldek")).request_body("seedance-2.0", payload)
        toonflow = ToonflowAdapter(config("toonflow")).request_body("Seedance 2.0", payload)

        self.assertIs(paisio["generate_audio"], True)
        self.assertIs(rolldek["generate_audio"], True)
        self.assertIs(toonflow["metadata"]["generate_audio"], True)

    def test_false_is_not_silently_changed_to_true(self):
        payload = {
            "prompt": "无声视频",
            "duration": 4,
            "generate_audio": False,
            "_route": {"resolution": "720p"},
        }

        self.assertIs(
            PaisioAdapter(config("paisio")).request_body("sd2-720p", payload)[
                "generate_audio"
            ],
            False,
        )

    def test_toonflow_forwards_reference_audio_as_official_multimodal_item(self):
        payload = {
            "prompt": "follow the reference voice rhythm",
            "duration": 4,
            "aspect_ratio": "16:9",
            "generate_audio": True,
            "images": [{"url": "https://media.example.com/frame.png", "role": "reference"}],
            "audios": [{"url": "https://media.example.com/voice.mp3", "role": "reference_audio"}],
            "_route": {"resolution": "720p"},
        }

        body = ToonflowAdapter(config("toonflow")).request_body("Seedance 2.0", payload)

        self.assertIn(
            {
                "type": "audio_url",
                "audio_url": {"url": "https://media.example.com/voice.mp3"},
                "role": "reference_audio",
            },
            body["metadata"]["references"],
        )
        self.assertIs(body["metadata"]["generate_audio"], True)


if __name__ == "__main__":
    unittest.main()
