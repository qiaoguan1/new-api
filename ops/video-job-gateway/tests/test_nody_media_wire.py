"""Exact, local-only contracts for installed Nody media request builders."""

import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from nody_media_wire import build_media_body


def asset(index=1, kind="image", role="reference"):
    extensions = {"image": "png", "video": "mp4", "audio": "mp3"}
    return {
        "url": f"https://media.example/{kind}{index}.{extensions[kind]}",
        "role": role,
        "identity": "a" * 64,
    }


def payload(model_id="wan3.0-video", **changes):
    defaults = {
        "model": model_id,
        "mode": "reference",
        "prompt": "blue ball",
        "resolution": "720p",
        "duration": 6,
        "aspect_ratio": "16:9",
        "generate_audio": True,
        "images": [asset()],
        "videos": [],
        "audios": [],
    }
    if model_id == "flux-3-video":
        defaults.update(mode="first_frame", images=[asset(role="first_frame")])
    defaults.update(changes)
    return defaults


class NodyMediaWireTests(unittest.TestCase):
    def test_wan_frame_bodies_use_explicit_roles_and_no_reference_ratio(self):
        for model in ("wan3.0-video", "wan3.0-video-prime"):
            base = {
                "model": model, "prompt": "blue ball", "duration": 6,
                "resolution": "720P", "audio": False, "watermark": False,
                "generation_type": "frame",
            }
            cases = [
                ("first_frame", [asset(role="first_frame")], {"image_urls": [asset()["url"]]}),
                ("last_frame", [asset(role="last_frame")], {
                    "image_with_roles": [{"url": asset()["url"], "role": "last_frame"}],
                }),
                ("first_last_frame", [asset(2, role="last_frame"), asset(role="first_frame")], {
                    "image_urls": [asset()["url"], asset(2)["url"]],
                }),
            ]
            for mode, images, expected_assets in cases:
                with self.subTest(model=model, mode=mode):
                    actual = build_media_body(model, payload(model, mode=mode, images=images, generate_audio=False))
                    self.assertEqual(actual, {**base, **expected_assets})

    def test_wan_multimodal_body_keeps_all_materials_and_generation_audio(self):
        for model in ("wan3.0-video", "wan3.0-video-prime"):
            with self.subTest(model=model):
                data = payload(model, mode="all_reference", resolution="1080p", duration=30,
                               images=[asset(1), asset(2)], videos=[asset(kind="video")], audios=[asset(kind="audio")])
                before = copy.deepcopy(data)
                self.assertEqual(build_media_body(model, data), {
                    "model": model, "prompt": "blue ball", "duration": 30,
                    "resolution": "1080P", "audio": True, "watermark": False,
                    "generation_type": "reference", "size": "16:9",
                    "image_urls": [asset()["url"], asset(2)["url"]],
                    "video_urls": [asset(kind="video")["url"]],
                    "audio_urls": [asset(kind="audio")["url"]],
                })
                self.assertEqual(data, before)

    def test_wan_allows_empty_prompt_with_a_reference_asset(self):
        actual = build_media_body("wan3.0-video", payload(prompt="", images=[], audios=[asset(kind="audio")]))
        self.assertNotIn("prompt", actual)
        self.assertEqual(actual["audio_urls"], [asset(kind="audio")["url"]])

    def test_wan_maximum_counts_are_preserved_without_truncation(self):
        data = payload(mode="all_reference", images=[asset(i) for i in range(10)],
                       videos=[asset(i, "video") for i in range(5)], audios=[asset(i, "audio") for i in range(5)])
        body = build_media_body("wan3.0-video", data)
        for source, target in (("images", "image_urls"), ("videos", "video_urls"), ("audios", "audio_urls")):
            self.assertEqual(body[target], [item["url"] for item in data[source]])

    def test_reference_modes_count_all_material_types_not_just_images(self):
        for changes in [
            {"mode": "reference", "images": [asset()], "audios": [asset(kind="audio")]},
            {"mode": "all_reference", "images": [asset()]},
            {"mode": "all_reference", "images": [], "videos": [asset(kind="video")]},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_media_body("wan3.0-video", payload(**changes))

    def test_supported_resolution_and_duration_boundaries_are_not_coerced(self):
        cases = [
            ("wan3.0-video", {"resolution": "480p", "duration": 2}),
            ("wan3.0-video-prime", {"resolution": "1080p", "duration": 30}),
            ("omni-flash", {"resolution": "1080p", "duration": 4}),
            ("omni-flash", {"images": [], "videos": [asset(kind="video")], "resolution": "4k", "duration": 30}),
            ("flux-3-video", {"resolution": "720p", "duration": 5, "aspect_ratio": "adaptive"}),
            ("grok-video-3", {"duration": 30}),
            ("grok-imagine-1.5-video", {"resolution": "480p", "duration": 30}),
            ("grok-imagine-video-official", {"resolution": "480p", "duration": 1}),
            ("grok-imagine-video-official", {"resolution": "720p", "duration": 15}),
        ]
        for model, changes in cases:
            with self.subTest(model=model, changes=changes):
                actual = build_media_body(model, payload(model, **changes))
                self.assertEqual(actual["duration"], changes["duration"])
                if model == "flux-3-video":
                    self.assertEqual(actual["aspect_ratio"], "auto")

    def test_omni_reference_and_video_edit_have_no_invented_audio_field(self):
        cases = [
            payload("omni-flash", duration=4),
            payload("omni-flash", mode="all_reference", duration=10, resolution="4k", images=[asset(i) for i in range(1, 4)]),
            payload("omni-flash", images=[], videos=[asset(kind="video")], duration=7),
        ]
        for data in cases:
            with self.subTest(data=data):
                expected = {"model": "omni-flash", "prompt": "blue ball", "duration": data["duration"],
                            "resolution": data["resolution"], "aspect_ratio": "16:9"}
                if data["images"]:
                    expected["image_urls"] = [item["url"] for item in data["images"]]
                if data["videos"]:
                    expected["video_urls"] = [item["url"] for item in data["videos"]]
                self.assertEqual(build_media_body("omni-flash", data), expected)

    def test_flux_frame_and_multi_reference_bodies_preserve_false_audio(self):
        for mode, images in [
            ("first_frame", [asset(role="first_frame")]),
            ("first_last_frame", [asset(role="first_frame"), asset(2, role="last_frame")]),
            ("all_reference", [asset(i) for i in range(1, 11)]),
        ]:
            with self.subTest(mode=mode):
                self.assertEqual(build_media_body("flux-3-video", payload(
                    "flux-3-video", mode=mode, images=images, duration=20, resolution="1080p", generate_audio=False,
                )), {
                    "model": "flux-3-video", "prompt": "blue ball", "duration": 20,
                    "resolution": "1080p", "aspect_ratio": "16:9", "audio": False,
                    "safety_tolerance": 4, "image_urls": [item["url"] for item in images],
                })

    def test_grok_variant_specific_single_and_multiple_image_fields(self):
        models = ["grok-video-3", "grok-imagine-1.5-video", "grok-imagine-video-official"]
        for model in models:
            for count in (1, 7):
                with self.subTest(model=model, count=count):
                    data = payload(model, mode="reference" if count == 1 else "all_reference",
                                   images=[asset(i) for i in range(1, count + 1)])
                    urls = [item["url"] for item in data["images"]]
                    expected = {"model": model, "prompt": "blue ball", "duration": 6}
                    if model == "grok-video-3":
                        expected.update(images=urls, resolution="720P")
                        if count > 1:
                            expected["ratio"] = "16:9"
                    elif model == "grok-imagine-1.5-video":
                        expected.update(image_urls=urls, quality="720p")
                        if count > 1:
                            expected["size"] = "16:9"
                    else:
                        expected["resolution"] = "720p"
                        if count == 1:
                            expected["image"] = {"url": urls[0]}
                        else:
                            expected["reference_images"] = [{"url": url} for url in urls]
                            expected["aspect_ratio"] = "16:9"
                    self.assertEqual(build_media_body(model, data), expected)

    def test_invalid_spec_types_values_and_unsupported_controls_are_rejected(self):
        cases = [
            ("wan3.0-video", {"resolution": "720P"}),
            ("wan3.0-video", {"resolution": "4k"}),
            ("wan3.0-video", {"duration": -1}),
            ("wan3.0-video", {"duration": 31}),
            ("wan3.0-video", {"duration": "6"}),
            ("wan3.0-video", {"duration": 6.0}),
            ("wan3.0-video", {"duration": True}),
            ("wan3.0-video", {"duration": float("inf")}),
            ("wan3.0-video", {"duration": float("nan")}),
            ("wan3.0-video", {"aspect_ratio": "7:5"}),
            ("wan3.0-video", {"generate_audio": "off"}),
            ("wan3.0-video", {"generate_audio": 1}),
            ("wan3.0-video", {"generate_audio": None}),
            ("wan3.0-video", {"prompt": None}),
            ("wan3.0-video", {"model": "wan3.0-video-prime"}),
            ("omni-flash", {"duration": 0}),
            ("omni-flash", {"duration": 7}),
            ("omni-flash", {"duration": 31, "images": [], "videos": [asset(kind="video")]}),
            ("omni-flash", {"aspect_ratio": "1:1"}),
            ("omni-flash", {"generate_audio": False}),
            ("flux-3-video", {"duration": 4}),
            ("flux-3-video", {"duration": 21}),
            ("flux-3-video", {"resolution": "480p"}),
            ("flux-3-video", {"prompt": ""}),
            ("grok-video-3", {"duration": 7}),
            ("grok-video-3", {"resolution": "480p"}),
            ("grok-imagine-1.5-video", {"duration": 5}),
            ("grok-imagine-video-official", {"duration": 16}),
            ("grok-imagine-video-official", {"generate_audio": False}),
            ("grok-imagine-video-official", {"prompt": "  "}),
        ]
        for model, changes in cases:
            with self.subTest(model=model, changes=changes), self.assertRaises(ValueError):
                build_media_body(model, payload(model, **changes))

    def test_frame_and_reference_counts_roles_and_unsupported_media_are_rejected(self):
        cases = [
            ("wan3.0-video", {"mode": "text"}),
            ("unknown-video", {}),
            ("wan3.0-video", {"mode": "first_frame", "images": [asset()]}),
            ("wan3.0-video", {"mode": "first_frame", "images": [asset(role="first_frame"), asset(2, role="first_frame")]}),
            ("wan3.0-video", {"mode": "first_frame", "images": [asset(role="first_frame")], "videos": [asset(kind="video")]}),
            ("wan3.0-video", {"mode": "first_last_frame", "images": [asset(role="first_frame")]}),
            ("wan3.0-video", {"mode": "first_last_frame", "images": [asset(role="first_frame"), asset(2, role="first_frame")]}),
            ("wan3.0-video", {"images": []}),
            ("wan3.0-video", {"images": [asset(role="first_frame")]}),
            ("wan3.0-video", {"mode": "all_reference", "images": [asset(i) for i in range(11)]}),
            ("wan3.0-video", {"mode": "all_reference", "videos": [asset(i, "video") for i in range(6)]}),
            ("wan3.0-video", {"mode": "all_reference", "audios": [asset(i, "audio") for i in range(6)]}),
            ("wan3.0-video", {"images": ["https://media.example/a.png"]}),
            ("wan3.0-video", {"videos": "https://media.example/a.mp4"}),
            ("wan3.0-video", {"audios": None}),
            ("omni-flash", {"mode": "all_reference", "images": [asset(), asset(2)]}),
            ("omni-flash", {"mode": "all_reference", "videos": [asset(1, "video"), asset(2, "video")]}),
            ("omni-flash", {"audios": [asset(kind="audio")]}),
            ("omni-flash", {"mode": "first_frame", "images": [asset(role="first_frame")]}),
            ("flux-3-video", {"mode": "last_frame", "images": [asset(role="last_frame")]}),
            ("flux-3-video", {"mode": "reference", "images": [asset()]}),
            ("flux-3-video", {"videos": [asset(kind="video")]}),
            ("flux-3-video", {"audios": [asset(kind="audio")]}),
            ("grok-video-3", {"mode": "all_reference", "images": [asset(i) for i in range(8)]}),
            ("grok-video-3", {"videos": [asset(kind="video")]}),
            ("grok-imagine-1.5-video", {"audios": [asset(kind="audio")]}),
            ("grok-imagine-video-official", {"mode": "first_frame", "images": [asset(role="first_frame")]}),
        ]
        for model, changes in cases:
            with self.subTest(model=model, changes=changes), self.assertRaises(ValueError):
                build_media_body(model, payload(model, **changes))

    def test_unsafe_asset_urls_are_rejected_without_network_access(self):
        urls = [
            "http://media.example/a.png", "https://user:pass@media.example/a.png",
            "https://127.0.0.1/a.png", "https://10.0.0.1/a.png", "https://169.254.169.254/a.png",
            "https://[::1]/a.png", "https://[::ffff:127.0.0.1]/a.png", "https://localhost/a.png",
            "https://media.local/a.png", "https://media.example/a.png#secret", "https://[not-ip]/a.png",
            "https://media.example:bad/a.png", "https://media.example\n/a.png",
            "https://2130706433/a.png", "https://127.1/a.png", "https://0x7f000001/a.png",
            "https://@media.example/a.png", "https://[fe80::1]/a.png", "https://0.0.0.0/a.png",
            "https://192.0.2.1/a.png", "https://[2001:db8::1]/a.png",
            "https://media.example:8443/a.png", "https://media.example:0/a.png",
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                build_media_body("wan3.0-video", payload(images=[{**asset(), "url": url}]))


if __name__ == "__main__":
    unittest.main()
