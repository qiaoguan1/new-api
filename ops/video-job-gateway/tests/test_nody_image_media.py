"""Image content must be verified before a paid Nody task is accepted."""

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
from reference_contract import ReferenceContractError, ReferenceMediaVerifier


class ImageMediaTests(unittest.TestCase):
    def verifier(self, data, mime="image/png"):
        verifier = ReferenceMediaVerifier(("media.example",))
        verifier._public_dns_addresses = lambda *_: ("8.8.8.8",)

        @contextmanager
        def response(*_, **__):
            stream = io.BytesIO(data)
            stream.headers = Message()
            stream.headers["Content-Type"] = mime
            stream.headers["Content-Length"] = str(len(data))
            yield stream

        verifier._open_pinned = response
        return verifier

    def test_verified_bytes_digest_type_and_dimensions_are_checked(self):
        data = b"\x89PNG\r\n\x1a\nfixture"
        probe = SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 640, "height": 360}]}))
        image = {"url": "https://media.example/a.png", "role": "reference", "identity": hashlib.sha256(data).hexdigest()}
        with patch("reference_contract.subprocess.run", return_value=probe):
            self.verifier(data).verify_images([image])
        with self.assertRaises(ReferenceContractError) as mismatch:
            self.verifier(data).verify_images([{**image, "identity": "a" * 64}])
        self.assertEqual(mismatch.exception.code, "video_image_identity_mismatch")

    def test_html_image_and_wrong_aspect_are_rejected(self):
        data = b"\x89PNG\r\n\x1a\nfixture"
        image = {"url": "https://media.example/a.png", "role": "reference", "identity": hashlib.sha256(data).hexdigest()}
        with self.assertRaises(ReferenceContractError) as mime:
            self.verifier(data, "text/html").verify_images([image])
        self.assertEqual(mime.exception.code, "video_image_format_invalid")
        probe = SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 512, "height": 512}]}))
        with patch("reference_contract.subprocess.run", return_value=probe), self.assertRaises(ReferenceContractError) as aspect:
            self.verifier(data).verify_images([image])
        self.assertEqual(aspect.exception.code, "video_image_aspect_ratio_unsupported")

    def test_unsafe_source_is_rejected_before_fetch(self):
        verifier = ReferenceMediaVerifier(("media.example",))
        image = {"url": "https://127.0.0.1/a.png", "role": "reference", "identity": "a" * 64}
        with patch.object(verifier, "_open_pinned") as fetch, self.assertRaises(ReferenceContractError):
            verifier.verify_images([image])
        fetch.assert_not_called()

    def test_playlist_disguised_as_image_cannot_reach_the_media_decoder(self):
        data = b'#EXTM3U\nhttp://127.0.0.1/private\n'
        image = {'url': 'https://media.example/a.png', 'role': 'reference', 'identity': hashlib.sha256(data).hexdigest()}
        with patch('reference_contract.subprocess.run') as probe, self.assertRaises(ReferenceContractError) as error:
            self.verifier(data).verify_images([image])
        self.assertEqual(error.exception.code, 'video_image_format_invalid')
        probe.assert_not_called()

    def test_malformed_ipv6_source_returns_a_controlled_validation_error(self):
        with self.assertRaises(ReferenceContractError) as error:
            ReferenceMediaVerifier(('media.example',)).verify_image_origins(['https://[not-ip]/a.png'])
        self.assertEqual(error.exception.code, 'video_image_url_invalid')


if __name__ == "__main__":
    unittest.main()
