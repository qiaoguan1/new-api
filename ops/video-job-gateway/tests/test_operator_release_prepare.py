"""Release preparation cannot make a paid provider request or replace a service."""
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

PATH = pathlib.Path(__file__).resolve().parents[3] / 'specs/186-nody-multimodal/prepare_operator_release.py'
spec = importlib.util.spec_from_file_location('prepare_operator_under_test', PATH)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationSafetyTests(unittest.TestCase):
    def test_generation_or_other_mutating_endpoints_are_rejected_before_network(self):
        with patch.object(prepare.urllib.request, 'build_opener') as transport:
            for endpoint in ('/v2/videos/generations', '/v1/videos', '/api/token/', '/api/user/self?recharge=1'):
                with self.subTest(endpoint=endpoint), self.assertRaises(RuntimeError):
                    prepare.fetch(endpoint, {})
            transport.assert_not_called()

    def test_inspection_is_restricted_to_exact_three_owned_release_targets(self):
        with patch.object(prepare, 'run') as command:
            for name in ('ai-api-stack-new-api-1', '/', 'other-user-container'):
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    prepare.inspect(name)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
