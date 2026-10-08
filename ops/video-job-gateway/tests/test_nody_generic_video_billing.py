"""Provider platform48 video receipts use action=generate for image inputs."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from billing_collectors import BillingCollectionError, NewAPITaskBillingCollector


class NodyGenericBillingTests(unittest.TestCase):
    def row(self):
        return {'task_id': '1d9ddf27-f609-47f9-ace6-59de4919df4f', 'platform': '48', 'action': 'generate',
                'status': 'SUCCESS', 'quota': 150000, 'finish_time': 1791350000,
                'properties': {'origin_model_name': 'grok-imagine-video-official', 'request_url_path': '/v2/videos/generations', 'video_has_input': False}}

    def test_exact_nody_generic_video_receipt_uses_actual_quota_cost(self):
        collector = NewAPITaskBillingCollector('nodyhub', 'https://nodyhub.com/api/task/self', rate_cny_per_usd='1.5')
        row = self.row()
        result = collector._parse_newapi_record({'success': True, 'data': {'total': 1, 'items': [row]}}, row['task_id'])
        self.assertEqual(result.actual_cost_cny_exact, '0.450000')

    def test_generic_image_or_wrong_platform_is_not_a_video_bill(self):
        collector = NewAPITaskBillingCollector('nodyhub', 'https://nodyhub.com/api/task/self', rate_cny_per_usd='1.5')
        for changed in [{"platform": "39"}, {"properties": {"origin_model_name": "gpt-image-2.5-flare", "request_url_path": "/v1/images/generations"}},
                        {"properties": {"origin_model_name": "grok-imagine-video-official", "request_url_path": "/v1/images/generations"}}]:
            with self.subTest(changed=changed), self.assertRaises(BillingCollectionError):
                row = {**self.row(), **changed}
                collector._parse_newapi_record({'success': True, 'data': {'total': 1, 'items': [row]}}, row['task_id'])


if __name__ == '__main__':
    unittest.main()
