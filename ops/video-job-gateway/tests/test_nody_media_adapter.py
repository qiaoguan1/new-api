"""Provider adapter sends the validated media body once, without text drift."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from adapters import AdapterError, JsonResponse, ProviderConfig, TransportFailure
from nodyhub import NodyHubAdapter


class Transport:
    def __init__(self, failure=False):
        self.calls = []
        self.failure = failure

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.failure:
            raise TransportFailure("timeout")
        return JsonResponse(200, {}, {"id": "f38d6547-27af-4f3b-8945-8c643162c432", "status": "queued"}, "")


class MediaAdapterTests(unittest.TestCase):
    def adapter(self, transport):
        return NodyHubAdapter(ProviderConfig("nodyhub", "https://nodyhub.com", "test", ()), transport)

    def payload(self, **changes):
        return {"prompt": "a blue ball", "duration": 2, "resolution": "480p", "aspect_ratio": "16:9",
                "mode": "all_reference", "generate_audio": True,
                "images": [{"url": "https://media.example/a.png", "role": "reference", "identity": "a" * 64}],
                "videos": [{"url": "https://media.example/a.mp4", "role": "reference", "identity": "b" * 64}],
                "audios": [{"url": "https://media.example/a.mp3", "role": "reference", "identity": "c" * 64}], **changes}

    def test_wan_mixed_reference_and_frame_wire_with_no_private_metadata(self):
        for model in ("wan3.0-video", "wan3.0-video-prime"):
            transport = Transport()
            self.adapter(transport).submit("request", model, self.payload())
            method, url, kwargs = transport.calls[0]
            self.assertEqual((method, url), ("POST", "https://nodyhub.com/v2/videos/generations"))
            self.assertEqual(kwargs["payload"], {
                "model": model, "prompt": "a blue ball", "duration": 2, "resolution": "480P",
                "audio": True, "watermark": False, "size": "16:9", "generation_type": "reference",
                "image_urls": ["https://media.example/a.png"], "video_urls": ["https://media.example/a.mp4"],
                "audio_urls": ["https://media.example/a.mp3"],
            })

    def test_omni_video_only_and_flux_first_last_route_v2(self):
        for model, payload, expected in (
            ("omni-flash", self.payload(mode="reference", images=[], audios=[], duration=4, resolution="720p"),
             {"model": "omni-flash", "prompt": "a blue ball", "duration": 4, "resolution": "720p",
              "aspect_ratio": "16:9", "video_urls": ["https://media.example/a.mp4"]}),
            ("flux-3-video", self.payload(mode="first_last_frame", videos=[], audios=[], duration=5,
              resolution="720p", aspect_ratio="adaptive", generate_audio=False, images=[
                  {"url": "https://media.example/last.png", "role": "last_frame"},
                  {"url": "https://media.example/first.png", "role": "first_frame"}]),
             {"model": "flux-3-video", "prompt": "a blue ball", "duration": 5, "resolution": "720p",
              "aspect_ratio": "auto", "audio": False, "safety_tolerance": 4,
              "image_urls": ["https://media.example/first.png", "https://media.example/last.png"]}),
        ):
            transport = Transport()
            self.adapter(transport).submit("request", model, payload)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(transport.calls[0][2]["payload"], expected)

    def test_invalid_media_never_calls_provider_and_timeout_remains_uncertain(self):
        for change in ({"videos": [{"url": "https://media.example:8443/a.mp4", "role": "reference"}]},
                       {"file_url": "https://media.example/hidden.zip"}, {"link_url": "https://media.example/hidden"}):
            transport = Transport()
            with self.assertRaises(AdapterError):
                self.adapter(transport).submit("request", "wan3.0-video", self.payload(**change))
            self.assertEqual(transport.calls, [])
        transport = Transport(failure=True)
        with self.assertRaises(AdapterError) as caught:
            self.adapter(transport).submit("request", "wan3.0-video", self.payload())
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
