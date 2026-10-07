"""Provider V1.0.0 image-reference wire contracts, without paid requests."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import AdapterError, JsonResponse, ProviderConfig
from nodyhub import NodyHubAdapter


class Transport:
    def __init__(self):
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return JsonResponse(200, {}, {"id": "f38d6547-27af-4f3b-8945-8c643162c432", "status": "queued"}, "")


class NodyImageContractTests(unittest.TestCase):
    def test_documented_single_image_reference_has_exact_model_specific_fields(self):
        cases = [
            ("grok-video-3", "720p", 6, {"resolution": "720P", "duration": 6, "images": ["https://media.example/a.png"]}),
            ("grok-imagine-1.5-video", "720p", 6, {"quality": "720p", "duration": 6, "image_urls": ["https://media.example/a.png"]}),
            ("grok-imagine-video-official", "480p", 1, {"resolution": "480p", "duration": 1, "image": {"url": "https://media.example/a.png"}}),
        ]
        for model, resolution, duration, fields in cases:
            with self.subTest(model=model):
                transport = Transport()
                adapter = NodyHubAdapter(ProviderConfig("nodyhub", "https://nodyhub.com", "test", ()), transport)
                payload = {"prompt": "a blue ball", "resolution": resolution, "duration": duration,
                           "aspect_ratio": "16:9", "mode": "reference", "generate_audio": True,
                           "images": [{"url": "https://media.example/a.png", "role": "reference", "identity": "a" * 64}]}
                adapter.submit("test", model, payload)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(transport.calls[0][1], "https://nodyhub.com/v2/videos/generations")
                self.assertEqual(transport.calls[0][2]["payload"], {"model": model, "prompt": "a blue ball", **fields})

    def test_documented_multiple_references_keep_order_and_use_matching_ratio_field(self):
        cases = [
            ("grok-video-3", "720p", 6, "images", "ratio"),
            ("grok-imagine-1.5-video", "720p", 6, "image_urls", "size"),
            ("grok-imagine-video-official", "480p", 1, "reference_images", "aspect_ratio"),
        ]
        urls = ["https://media.example/first-reference.png", "https://media.example/second-reference.png"]
        for model, resolution, duration, image_field, ratio_field in cases:
            with self.subTest(model=model):
                transport = Transport()
                adapter = NodyHubAdapter(ProviderConfig("nodyhub", "https://nodyhub.com", "test", ()), transport)
                payload = {"prompt": "a blue ball", "resolution": resolution, "duration": duration,
                           "aspect_ratio": "16:9", "mode": "all_reference", "generate_audio": True,
                           "images": [{"url": url, "role": "reference", "identity": str(index) * 64} for index, url in enumerate(urls, 1)]}
                adapter.submit("test", model, payload)
                wire = transport.calls[0][2]["payload"]
                self.assertEqual(wire[ratio_field], "16:9")
                expected = [{"url": url} for url in urls] if image_field == "reference_images" else urls
                self.assertEqual(wire[image_field], expected)

    def test_undocumented_first_last_and_audio_video_inputs_never_submit(self):
        for change in [{"mode": "first_last_frame"}, {"videos": [{"url": "https://media.example/a.mp4"}]},
                       {"audios": [{"url": "https://media.example/a.mp3"}]}]:
            with self.subTest(change=change):
                transport = Transport()
                adapter = NodyHubAdapter(ProviderConfig("nodyhub", "https://nodyhub.com", "test", ()), transport)
                payload = {"prompt": "a blue ball", "resolution": "480p", "duration": 1,
                           "aspect_ratio": "16:9", "mode": "reference", "generate_audio": True,
                           "images": [{"url": "https://media.example/a.png", "role": "reference"}], **change}
                with self.assertRaises(AdapterError):
                    adapter.submit("test", "grok-imagine-video-official", payload)
                self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
