"""Documented video UI actions require the same authenticated model/path binding."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from billing_collectors import BillingCollectionError, NewAPITaskBillingCollector


class VideoActionBillingTests(unittest.TestCase):
    def test_media_actions_keep_exact_task_model_platform_and_endpoint_binding(self):
        collector = NewAPITaskBillingCollector("nodyhub", "https://nodyhub.com/api/task/self", rate_cny_per_usd="1.5")
        task = "f38d6547-27af-4f3b-8945-8c643162c432"
        for action in ("firstTailGenerate", "referenceGenerate", "remixGenerate"):
            row = {"task_id": task, "action": action, "platform": 48, "status": "SUCCESS", "quota": 400000,
                   "finish_time": 1790170226, "properties": {"origin_model_name": "wan3.0-video", "request_url_path": "/v2/videos/generations"}}
            raw = {"success": True, "data": {"total": 1, "items": [row]}}
            with self.subTest(action=action):
                self.assertEqual(collector._parse_newapi_record(raw, task).actual_cost_cny_exact, "1.200000")
            for changes in ({"platform": 1}, {"task_id": "another-task"}, {"properties": {"origin_model_name": "grok-image", "request_url_path": "/v2/videos/generations"}},
                            {"properties": {"origin_model_name": "wan3.0-video", "request_url_path": "/v1/images/generations"}}):
                with self.subTest(action=action, changes=changes), self.assertRaises(BillingCollectionError):
                    collector._parse_newapi_record({"success": True, "data": {"total": 1, "items": [{**row, **changes}]}}, task)


if __name__ == "__main__":
    unittest.main()
