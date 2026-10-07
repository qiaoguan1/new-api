"""Nody v2 image jobs preserve their original UUID and documented result shapes."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from adapters import JsonResponse, ProviderConfig
from nodyhub import NodyHubAdapter

TASK = '1d9ddf27-f609-47f9-ace6-59de4919df4f'
URL = 'https://getapib.org/video.mp4'


class PollTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url))
        return JsonResponse(200, {}, self.responses.pop(0), '')


class NodyPollContractTests(unittest.TestCase):
    def test_legacy_output_only_query_delivers_the_existing_video(self):
        transport = PollTransport([{'output': {'url': URL}}])
        adapter = NodyHubAdapter(ProviderConfig('nodyhub', 'https://nodyhub.com', 'test', ('getapib.org',)), transport)
        result = adapter.poll(TASK)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.result_url, URL)
        self.assertEqual(result.upstream_task_id, TASK)
        self.assertEqual(transport.calls, [('GET', 'https://nodyhub.com/v1/videos/' + TASK)])

    def test_nested_documented_results_are_delivered_without_another_submission(self):
        transport = PollTransport([{'data': {'id': 'raw-provider-id', 'status': 'SUCCESS', 'result': {'videos': [{'url': [URL]}]}}}])
        adapter = NodyHubAdapter(ProviderConfig('nodyhub', 'https://nodyhub.com', 'test', ('getapib.org',)), transport)
        result = adapter.poll(TASK)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.result_url, URL)
        self.assertEqual(result.upstream_task_id, TASK)
        self.assertEqual(len(transport.calls), 1)


if __name__ == '__main__':
    unittest.main()
