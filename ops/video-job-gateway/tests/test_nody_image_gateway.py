"""Nody image admission is explicit, evidenced and verified before task freeze."""

import json
import pathlib
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapters import ProviderConfig
from app import Config, Gateway, GatewayError
from nodyhub import NodyHubAdapter
from reference_contract import ReferenceContractError


class GatewayImageTests(unittest.TestCase):
    def gateway(self, directory):
        path = pathlib.Path(directory) / 'image-contracts.json'
        path.write_text(json.dumps({'schema_version': 'xtai-nody-image-input-v1', 'revision': 'fixture-verified', 'profiles': [{
            'model': 'grok-imagine-video-official', 'mode': 'reference', 'image_count': 1, 'resolution': '480p', 'duration': 1,
            'actual_cost_cny_exact': '0.300000', 'evidence_task_id': 'f38d6547-27af-4f3b-8945-8c643162c432',
            'evidence_source': 'nodyhub_authenticated_video_task', 'status': 'succeeded', 'cost_status': 'actual',
        }]}))
        provider = ProviderConfig('nodyhub', 'https://nodyhub.com', 'test', ('getapib.org',))
        verifier = SimpleNamespace(verify_images=Mock())
        config = Config(token='test', data_dir=pathlib.Path(directory) / 'state', catalog_file=ROOT / 'catalog.json',
                        providers={'nodyhub': provider}, pricing_file=ROOT / 'relay-pricing.json',
                        public_base_url='https://api.aixingtuyun.com', v21_approved_providers=frozenset({'nodyhub'}),
                        nody_image_contract_file=path, reference_media_hosts=('media.example',))
        gateway = Gateway(config, adapters={'nodyhub': NodyHubAdapter(provider)},
                          billing_collectors={'nodyhub': SimpleNamespace(ready=True)}, reference_verifier=verifier, start_monitor=False)
        gateway.start_submit = Mock()
        return gateway, verifier

    def body(self):
        return {'provider_id': 'video-aixingtu-api', 'request_id': 'nody-image-case', 'model': 'grok-imagine-video-official',
                'prompt': 'a blue ball', 'resolution': '480p', 'duration': 1, 'aspect_ratio': '16:9', 'generate_audio': True,
                'mode': 'reference', 'images': ['https://media.example/a.png?signature=one'],
                'image_roles': ['reference'], 'image_identities': ['a' * 64]}

    def test_preflight_verifies_without_creating_or_submitting_a_task(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway, verifier = self.gateway(directory)
            result = gateway.preflight_nody_image(self.body())
            self.assertEqual(result['reserved_cny_exact'], '0.450000')
            self.assertIsNone(gateway.store.get(request_id=self.body()['request_id']))
            gateway.start_submit.assert_not_called()
            verifier.verify_images.assert_called_once()

    def test_invalid_image_fails_before_store_or_provider_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway, verifier = self.gateway(directory)
            verifier.verify_images.side_effect = ReferenceContractError('video_image_identity_mismatch', 'mismatch')
            body = self.body()
            with self.assertRaises(GatewayError) as failure:
                gateway.submit_v22(body, idempotency_key=body['request_id'])
            self.assertEqual(failure.exception.code, 'video_image_identity_mismatch')
            self.assertIsNone(gateway.store.get(request_id=body['request_id']))
            gateway.start_submit.assert_not_called()

    def test_explicit_reference_uses_mode_price_and_rotated_url_reuses_one_task(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway, verifier = self.gateway(directory)
            body = self.body()
            first, reused = gateway.submit_v22(body, idempotency_key=body['request_id'])
            self.assertFalse(reused)
            self.assertEqual(first['billing']['reserved_amount'], '0.450000')
            body['images'] = ['https://media.example/a.png?signature=two']
            second, reused = gateway.submit_v22(body, idempotency_key=body['request_id'])
            self.assertTrue(reused)
            self.assertEqual(first['job_id'], second['job_id'])
            gateway.start_submit.assert_called_once()
            verifier.verify_images.assert_called_once()
            body['image_roles'] = ['first']
            with self.assertRaises(GatewayError):
                gateway.submit_v22(body, idempotency_key=body['request_id'])

    def test_legacy_text_price_and_unsupported_modes_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway, verifier = self.gateway(directory)
            body = self.body()
            for field in ['mode', 'images', 'image_roles', 'image_identities']:
                body.pop(field)
            first, _ = gateway.submit_v22(body, idempotency_key=body['request_id'])
            self.assertEqual(first['billing']['reserved_amount'], '0.675000')
            verifier.verify_images.assert_not_called()
            bad = {**self.body(), 'request_id': 'bad-mode', 'mode': 'first_last_frame',
                   'images': ['https://media.example/a.png', 'https://media.example/b.png'],
                   'image_roles': ['first', 'last'], 'image_identities': ['a' * 64, 'b' * 64]}
            with self.assertRaises(GatewayError):
                gateway.submit_v22(bad, idempotency_key=bad['request_id'])
            self.assertIsNone(gateway.store.get(request_id=bad['request_id']))

    def test_accepted_image_replay_survives_removed_contract_and_unavailable_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway, verifier = self.gateway(directory)
            body = self.body()
            first, _ = gateway.submit_v22(body, idempotency_key=body['request_id'])
            config = replace(gateway.config, nody_image_contract_file=None)
            with patch.object(Gateway, 'start_submit'):
                restarted = Gateway(config, adapters=gateway.adapters,
                                    billing_collectors={'nodyhub': SimpleNamespace(ready=False)},
                                    reference_verifier=verifier, start_monitor=False)
            restarted.start_submit = Mock()
            body['images'] = ['https://media.example/a.png?signature=rotated']
            second, reused = restarted.submit_v22(body, idempotency_key=body['request_id'])
            self.assertTrue(reused)
            self.assertEqual(second['job_id'], first['job_id'])
            restarted.start_submit.assert_not_called()
            verifier.verify_images.assert_called_once()
            body['image_identities'] = ['b' * 64]
            with self.assertRaises(GatewayError) as conflict:
                restarted.submit_v22(body, idempotency_key=body['request_id'])
            self.assertEqual(int(conflict.exception.status), 409)


if __name__ == '__main__':
    unittest.main()
