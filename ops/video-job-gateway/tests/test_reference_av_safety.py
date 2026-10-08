"""AV references cannot escape their total I/O budget or invoke network demuxers."""

import hashlib
import io
import json
import pathlib
import subprocess
import sys
import unittest
from email.message import Message
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from reference_contract import ReferenceContractError, ReferenceMediaVerifier


MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
MP3 = b"\xff\xfb\x90\x64" + b"fixture"
WAV = b"RIFF\x10\x00\x00\x00WAVEfmt "
AAC = b"\xff\xf1\x50\x80\x01\x7f\xfc" + b"fixture"


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class Response(io.BytesIO):
    def __init__(self, data, mime, clock, *, read_seconds=0, read_error=None):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = mime
        self.headers["Content-Length"] = str(len(data))
        self.status = 200
        self.clock = clock
        self.read_seconds = read_seconds
        self.read_error = read_error
        self.sizes = []
        self.full_reads = 0
        self.sock = Mock()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.sock))

    def read(self, size=-1):
        self.full_reads += 1
        return self.read1(size)

    def read1(self, size=-1):
        self.sizes.append(size)
        self.clock.now += self.read_seconds
        if self.read_error is not None:
            raise self.read_error
        return super().read(size)


def media_item(data, kind="video", codec="mp3"):
    item = {
        "url": "https://media.example/reference",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "duration_seconds": "4.000000",
    }
    if kind == "video":
        item.update(mime_type="video/mp4", width_pixels=640, height_pixels=360)
    else:
        item.update(
            mime_type={"mp3": "audio/mpeg", "wav": "audio/wav", "aac": "audio/aac", "m4a": "audio/mp4"}[codec],
            codec=codec,
            sample_rate_hz=44100,
            channels=2,
        )
    return item


def probe_result(kind="video", codec="mp3"):
    stream = {"codec_type": kind}
    if kind == "video":
        stream.update(codec_name="h264", width=640, height=360)
        container = "mov,mp4,m4a,3gp,3g2,mj2"
    else:
        stream.update(codec_name="aac" if codec in {"aac", "m4a"} else codec, sample_rate="44100", channels=2)
        container = "mov,mp4,m4a,3gp,3g2,mj2" if codec == "m4a" else codec
    return SimpleNamespace(stdout=json.dumps({"streams": [stream], "format": {"format_name": container, "duration": "4.000000"}}))


class ReferenceAVSafetyTests(unittest.TestCase):
    def connection(self, response):
        connection = Mock()
        connection.getresponse.return_value = response
        connection.sock = response.sock
        return connection

    def test_disguised_playlists_are_rejected_before_decoder_and_closed(self):
        data = b"#EXTM3U\nhttp://127.0.0.1/private\n"
        for kind, codec in (("video", "mp3"), ("audio", "mp3"), ("audio", "wav"), ("audio", "aac"), ("audio", "m4a")):
            with self.subTest(kind=kind, codec=codec):
                item = media_item(data, kind, codec)
                clock = Clock()
                response = Response(data, item["mime_type"], clock)
                connection = self.connection(response)
                verifier = ReferenceMediaVerifier(("media.example",))
                with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8",)), patch(
                    "reference_contract._PinnedHTTPSConnection", return_value=connection
                ), patch("reference_contract.subprocess.run", return_value=probe_result(kind, codec)) as probe:
                    with self.assertRaises(ReferenceContractError) as caught:
                        verifier._verify_item(item, kind, 1024)
                self.assertEqual(caught.exception.code, f"reference_{kind}_format_invalid")
                probe.assert_not_called()
                self.assertTrue(response.closed)
                connection.close.assert_called_once_with()

    def test_valid_container_signatures_keep_supported_av_formats(self):
        cases = (
            (MP4, "video", "mp3", "mov"),
            (MP3, "audio", "mp3", "mp3"),
            (b"ID3\x04\x00\x00\x00\x00\x00\x00" + MP3, "audio", "mp3", "mp3"),
            (WAV, "audio", "wav", "wav"),
            (AAC, "audio", "aac", "aac"),
            (MP4, "audio", "m4a", "mov"),
        )
        for data, kind, codec, demuxer in cases:
            with self.subTest(kind=kind, codec=codec, data=data):
                item = media_item(data, kind, codec)
                clock = Clock()
                response = Response(data, item["mime_type"], clock)
                verifier = ReferenceMediaVerifier(("media.example",))
                with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8",)), patch(
                    "reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)
                ), patch("reference_contract.subprocess.run", return_value=probe_result(kind, codec)) as probe:
                    verifier._verify_item(item, kind, 1024)
                command = probe.call_args.args[0]
                self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file,pipe")
                self.assertEqual(command[command.index("-f") + 1], demuxer)
                self.assertTrue(response.closed)

    def test_wrong_magic_cannot_be_accepted_by_a_matching_mime(self):
        for data, kind, codec in ((AAC, "audio", "mp3"), (MP3, "audio", "aac"), (b"RIFF\x10\x00\x00\x00AVI ", "audio", "wav"), (b"not mp4", "video", "mp3")):
            with self.subTest(kind=kind, codec=codec):
                item = media_item(data, kind, codec)
                response = Response(data, item["mime_type"], Clock())
                verifier = ReferenceMediaVerifier(("media.example",))
                with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8",)), patch(
                    "reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)
                ), patch("reference_contract.subprocess.run", return_value=probe_result(kind, codec)) as probe:
                    with self.assertRaises(ReferenceContractError) as caught:
                        verifier._verify_item(item, kind, 1024)
                self.assertEqual(caught.exception.code, f"reference_{kind}_format_invalid")
                probe.assert_not_called()

    def test_all_av_assets_share_one_total_deadline(self):
        clock = Clock()
        video = media_item(MP4)
        audio = media_item(MP3, "audio")
        responses = [Response(MP4, "video/mp4", clock), Response(MP3, "audio/mpeg", clock)]
        connections = [self.connection(response) for response in responses]
        verifier = ReferenceMediaVerifier(("media.example",), timeout_seconds=5)

        def decode(*args, **kwargs):
            clock.now += 3
            return probe_result() if len(probe.call_args_list) == 1 else probe_result("audio")

        with patch("reference_contract.time.monotonic", side_effect=clock.monotonic), patch.object(
            verifier, "_public_dns_addresses", return_value=("8.8.8.8",)
        ), patch("reference_contract._PinnedHTTPSConnection", side_effect=connections) as connect, patch(
            "reference_contract.subprocess.run", side_effect=decode
        ) as probe:
            with self.assertRaises(ReferenceContractError) as caught:
                verifier.verify({"reference_videos": [video], "reference_audios": [audio]})
        self.assertEqual(caught.exception.code, "reference_audio_probe_failed")
        self.assertEqual(probe.call_args_list[1].kwargs["timeout"], 2)
        self.assertEqual(connect.call_args_list[1].kwargs["timeout"], 2)
        self.assertTrue(all(response.closed for response in responses))

    def test_deadline_is_not_restarted_for_the_next_reference(self):
        clock = Clock()
        video = media_item(MP4)
        audio = media_item(MP3, "audio")
        response = Response(MP4, "video/mp4", clock)
        verifier = ReferenceMediaVerifier(("media.example",), timeout_seconds=5)

        def decode(*args, **kwargs):
            clock.now = 5
            return probe_result()

        with patch("reference_contract.time.monotonic", side_effect=clock.monotonic), patch.object(
            verifier, "_public_dns_addresses", return_value=("8.8.8.8",)
        ), patch("reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)) as connect, patch(
            "reference_contract.subprocess.run", side_effect=decode
        ):
            with self.assertRaises(ReferenceContractError) as caught:
                verifier.verify({"reference_videos": [video], "reference_audios": [audio]})
        self.assertEqual(caught.exception.code, "reference_video_probe_failed")
        self.assertEqual(connect.call_count, 1)

    def test_slow_reads_expire_budget_and_close_without_decoder(self):
        clock = Clock()
        response = Response(MP4, "video/mp4", clock, read_seconds=5)
        connection = self.connection(response)
        verifier = ReferenceMediaVerifier(("media.example",), timeout_seconds=5)
        with patch("reference_contract.time.monotonic", side_effect=clock.monotonic), patch.object(
            verifier, "_public_dns_addresses", return_value=("8.8.8.8",)
        ), patch("reference_contract._PinnedHTTPSConnection", return_value=connection), patch("reference_contract.subprocess.run") as probe:
            with self.assertRaises(ReferenceContractError) as caught:
                verifier._verify_item(media_item(MP4), "video", 1024)
        self.assertEqual(caught.exception.code, "reference_video_probe_failed")
        probe.assert_not_called()
        self.assertEqual(len(response.sizes), 1)
        self.assertTrue(response.closed)
        connection.close.assert_called_once_with()

    def test_each_single_read_is_bounded_and_socket_budget_is_reduced(self):
        clock = Clock()
        data = MP4 + b"a" * (70 * 1024)
        response = Response(data, "video/mp4", clock, read_seconds=1)
        verifier = ReferenceMediaVerifier(("media.example",), timeout_seconds=5)
        with patch("reference_contract.time.monotonic", side_effect=clock.monotonic), patch.object(
            verifier, "_public_dns_addresses", return_value=("8.8.8.8",)
        ), patch("reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)), patch(
            "reference_contract.subprocess.run", return_value=probe_result()
        ) as probe:
            verifier._verify_item(media_item(data), "video", 100 * 1024)
        self.assertEqual(response.full_reads, 0)
        self.assertTrue(all(0 < size <= 64 * 1024 for size in response.sizes))
        self.assertEqual([call.args[0] for call in response.sock.settimeout.call_args_list], [5, 4, 3])
        self.assertEqual(probe.call_args.kwargs["timeout"], 2)

    def test_byte_limit_stops_stream_without_decoder(self):
        data = MP4 + b"overflow"
        response = Response(data, "video/mp4", Clock())
        response.headers.replace_header("Content-Length", str(len(MP4)))
        verifier = ReferenceMediaVerifier(("media.example",))
        with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8",)), patch(
            "reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)
        ), patch("reference_contract.subprocess.run") as probe:
            with self.assertRaises(ReferenceContractError) as caught:
                verifier._verify_item(media_item(MP4), "video", len(MP4))
        self.assertEqual(caught.exception.code, "reference_video_size_invalid")
        probe.assert_not_called()
        self.assertEqual(response.sizes, [len(MP4) + 1])
        self.assertTrue(response.closed)

    def test_existing_identity_mime_and_size_checks_still_block_decoder(self):
        cases = (
            ({"sha256": "a" * 64}, "video/mp4", "reference_video_identity_mismatch"),
            ({}, "text/html", "reference_video_format_invalid"),
            ({"size_bytes": len(MP4) + 1}, "video/mp4", "reference_video_size_invalid"),
        )
        for overrides, mime, code in cases:
            with self.subTest(code=code):
                response = Response(MP4, mime, Clock())
                verifier = ReferenceMediaVerifier(("media.example",))
                with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8",)), patch(
                    "reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)
                ), patch("reference_contract.subprocess.run") as probe:
                    with self.assertRaises(ReferenceContractError) as caught:
                        verifier._verify_item({**media_item(MP4), **overrides}, "video", 1024)
                self.assertEqual(caught.exception.code, code)
                probe.assert_not_called()
                self.assertTrue(response.closed)

    def test_read_error_is_controlled_and_not_retried_on_another_ip(self):
        response = Response(MP4, "video/mp4", Clock(), read_error=OSError("read failed"))
        connection = self.connection(response)
        verifier = ReferenceMediaVerifier(("media.example",))
        with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8", "1.1.1.1")), patch(
            "reference_contract._PinnedHTTPSConnection", return_value=connection
        ) as connect, patch("reference_contract.subprocess.run") as probe:
            with self.assertRaises(ReferenceContractError) as caught:
                verifier._verify_item(media_item(MP4), "video", 1024)
        self.assertEqual(caught.exception.code, "reference_video_probe_failed")
        self.assertEqual(connect.call_count, 1)
        self.assertTrue(response.closed)
        connection.close.assert_called_once_with()
        probe.assert_not_called()

    def test_decoder_timeout_is_controlled_and_closes_response(self):
        response = Response(MP4, "video/mp4", Clock())
        verifier = ReferenceMediaVerifier(("media.example",))
        with patch.object(verifier, "_public_dns_addresses", return_value=("8.8.8.8",)), patch(
            "reference_contract._PinnedHTTPSConnection", return_value=self.connection(response)
        ), patch("reference_contract.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 1)):
            with self.assertRaises(ReferenceContractError) as caught:
                verifier._verify_item(media_item(MP4), "video", 1024)
        self.assertEqual(caught.exception.code, "reference_video_probe_failed")
        self.assertTrue(response.closed)

    def test_probe_retains_declared_duration_dimensions_and_audio_properties(self):
        cases = (
            ("video", {"duration_seconds": "5.000000"}, "reference_video_duration_invalid"),
            ("video", {"width_pixels": 1280}, "reference_video_dimension_invalid"),
            ("audio", {"channels": 1}, "reference_audio_properties_invalid"),
        )
        for kind, overrides, code in cases:
            with self.subTest(code=code), patch("reference_contract.subprocess.run", return_value=probe_result(kind)):
                with self.assertRaises(ReferenceContractError) as caught:
                    ReferenceMediaVerifier._probe(pathlib.Path("fixture"), {**media_item(MP4 if kind == "video" else MP3, kind), **overrides}, kind)
                self.assertEqual(caught.exception.code, code)

    def test_probe_selects_one_fixed_field_track_and_discards_decoder_diagnostics(self):
        for kind, data, selector in (("video", MP4, "v:0"), ("audio", MP3, "a:0")):
            with self.subTest(kind=kind), patch("reference_contract.subprocess.run", return_value=probe_result(kind)) as probe:
                ReferenceMediaVerifier._probe(pathlib.Path("fixture"), media_item(data, kind), kind)
            command = probe.call_args.args[0]
            self.assertEqual(command[command.index("-select_streams") + 1], selector)
            self.assertNotIn("tags", command[command.index("-show_entries") + 1])
            self.assertNotIn("capture_output", probe.call_args.kwargs)
            self.assertEqual(probe.call_args.kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(probe.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_probe_rejects_more_than_one_track_or_malformed_selected_tracks(self):
        payload = json.loads(probe_result().stdout)
        selected = payload["streams"][0]
        for streams in ([selected, selected], [], ["not metadata"], [{"codec_type": "audio"}], {}):
            with self.subTest(streams=streams), patch("reference_contract.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps({**payload, "streams": streams}))):
                with self.assertRaises(ReferenceContractError) as caught:
                    ReferenceMediaVerifier._probe(pathlib.Path("fixture"), media_item(MP4), "video")
                self.assertEqual(caught.exception.code, "reference_video_format_invalid")

    def test_probe_rejects_overlarge_output_before_decoding_metadata(self):
        result = SimpleNamespace(stdout=probe_result().stdout + " " * (64 * 1024))
        with patch("reference_contract.subprocess.run", return_value=result):
            with self.assertRaises(ReferenceContractError) as caught:
                ReferenceMediaVerifier._probe(pathlib.Path("fixture"), media_item(MP4), "video")
        self.assertEqual(caught.exception.code, "reference_video_probe_failed")

    def test_probe_duration_is_finite_positive_and_bounded_with_controlled_errors(self):
        payload = json.loads(probe_result().stdout)
        for duration in ("N/A", "NaN", "sNaN", "Infinity", "-Infinity", "0", "-1", "15.000001", None, [], {}):
            altered = {**payload, "format": {**payload["format"], "duration": duration}}
            item = {**media_item(MP4), "duration_seconds": duration}
            with self.subTest(duration=duration), patch("reference_contract.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(altered))):
                with self.assertRaises(ReferenceContractError) as caught:
                    ReferenceMediaVerifier._probe(pathlib.Path("fixture"), item, "video")
                self.assertEqual(caught.exception.code, "reference_video_duration_invalid")

    def test_mixed_image_and_av_verification_share_the_caller_deadline(self):
        clock = Clock()
        image_data = b"\x89PNG\r\n\x1a\nfixture"
        responses = [Response(image_data, "image/png", clock), Response(MP4, "video/mp4", clock)]
        connections = [self.connection(response) for response in responses]
        image = {"url": "https://media.example/image.png", "identity": hashlib.sha256(image_data).hexdigest()}
        verifier = ReferenceMediaVerifier(("media.example",))

        def decode(*args, **kwargs):
            clock.now += 3
            if len(probe.call_args_list) == 1:
                return SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 640, "height": 360}]}))
            return probe_result()

        with patch("reference_contract.time.monotonic", side_effect=clock.monotonic), patch.object(
            verifier, "_public_dns_addresses", return_value=("8.8.8.8",)
        ), patch("reference_contract._PinnedHTTPSConnection", side_effect=connections), patch("reference_contract.subprocess.run", side_effect=decode) as probe:
            verifier.verify_images([image], deadline=5)
            with self.assertRaises(ReferenceContractError) as caught:
                verifier.verify({"reference_videos": [media_item(MP4)]}, deadline=5, duration_tolerance=Decimal("0.000001"))
        self.assertEqual(caught.exception.code, "reference_video_probe_failed")
        self.assertEqual(probe.call_args_list[1].kwargs["timeout"], 2)
        self.assertTrue(all(response.closed for response in responses))

    def test_elapsed_or_nonfinite_caller_deadlines_fail_before_dns_or_fetch(self):
        for method in ("verify_images", "verify"):
            for deadline in (-1, float("inf"), float("nan"), "20", True):
                verifier = ReferenceMediaVerifier(("media.example",))
                with self.subTest(method=method, deadline=deadline), patch.object(verifier, "_public_dns_addresses") as dns, patch.object(verifier, "_open_pinned") as fetch:
                    with self.assertRaises(ReferenceContractError):
                        if method == "verify_images":
                            verifier.verify_images([{"url": "https://media.example/image.png", "identity": "a" * 64}], deadline=deadline)
                        else:
                            verifier.verify({"reference_videos": [media_item(MP4)]}, deadline=deadline)
                dns.assert_not_called()
                fetch.assert_not_called()

    def test_caller_deadline_cannot_extend_default_image_and_av_ceilings(self):
        for method, data, mime, elapsed in (("verify_images", b"\x89PNG\r\n\x1a\nfixture", "image/png", 44), ("verify", MP4, "video/mp4", 59)):
            clock = Clock()
            response = Response(data, mime, clock)
            connection = self.connection(response)

            def getresponse():
                clock.now = elapsed
                return response

            connection.getresponse.side_effect = getresponse
            verifier = ReferenceMediaVerifier(("media.example",))
            result = SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 640, "height": 360}]})) if method == "verify_images" else probe_result()
            with self.subTest(method=method), patch("reference_contract.time.monotonic", side_effect=clock.monotonic), patch.object(
                verifier, "_public_dns_addresses", return_value=("8.8.8.8",)
            ), patch("reference_contract._PinnedHTTPSConnection", return_value=connection), patch("reference_contract.subprocess.run", return_value=result) as probe:
                if method == "verify_images":
                    verifier.verify_images([{"url": "https://media.example/image.png", "identity": hashlib.sha256(data).hexdigest()}], deadline=100)
                else:
                    verifier.verify({"reference_videos": [media_item(MP4)]}, deadline=100)
            self.assertEqual(probe.call_args.kwargs["timeout"], 1)


if __name__ == "__main__":
    unittest.main()
