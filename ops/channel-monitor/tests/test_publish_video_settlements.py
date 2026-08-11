import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "publish-video-settlements.py"
SPEC = importlib.util.spec_from_file_location("publish_video_settlements", MODULE_PATH)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublishSettlementTests(unittest.TestCase):
    def test_only_exact_completed_evidence_becomes_a_settlement(self):
        rows = [
            {
                "relay_job_id": "vjob_" + "1" * 32,
                "provider_id": "paisio",
                "upstream_task_id": "provider-1",
                "provider_task_id": "provider-1",
                "status": "succeeded",
                "provider_state": "completed",
                "match_status": "exact",
                "actual_cost_status": "actual",
                "upstream_actual_cost_cny": 1.45,
                "evidence_source": "provider_account_ledger",
                "fetched_at": 1786422600,
            },
            {
                "relay_job_id": "vjob_" + "2" * 32,
                "provider_id": "toonflow",
                "upstream_task_id": "",
                "provider_task_id": "provider-2",
                "status": "succeeded",
                "provider_state": "completed",
                "match_status": "inferred_unique",
                "actual_cost_status": "actual",
                "upstream_actual_cost_cny": 1.0,
                "evidence_source": "provider_account_ledger",
                "fetched_at": 1786422600,
            },
        ]

        requests = publisher.build_settlement_requests(rows)

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request["contract_version"], "xtai-video-billing-v2.1")
        self.assertEqual(request["actual_cost_cny_exact"], "1.450000")
        self.assertEqual(request["provider_task_id"], "provider-1")
        self.assertEqual(request["revision"], 1)
        self.assertRegex(request["settlement_id"], r"^[a-f0-9]{64}$")
        self.assertRegex(request["evidence_fingerprint"], r"^[a-f0-9]{64}$")
        self.assertNotIn("provider_id", request)

    def test_zero_verified_is_preserved_but_missing_cost_is_not_published(self):
        base = {
            "relay_job_id": "vjob_" + "3" * 32,
            "provider_id": "toonflow",
            "upstream_task_id": "provider-3",
            "provider_task_id": "provider-3",
            "status": "succeeded",
            "provider_state": "completed",
            "match_status": "exact",
            "evidence_source": "toonflow_web_operation_log",
            "fetched_at": 1786422600,
        }
        rows = [
            {**base, "actual_cost_status": "zero_verified", "upstream_actual_cost_cny": 0},
            {
                **base,
                "relay_job_id": "vjob_" + "4" * 32,
                "provider_task_id": "provider-4",
                "actual_cost_status": "unknown",
                "upstream_actual_cost_cny": None,
            },
        ]

        requests = publisher.build_settlement_requests(rows)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["actual_cost_status"], "zero_verified")
        self.assertEqual(requests[0]["actual_cost_cny_exact"], "0.000000")

    def test_newapi_pending_task_requires_one_exact_provider_record(self):
        pending = [
            {
                "contract_version": "xtai-video-billing-v2",
                "job_id": "task_" + "a" * 32,
                "provider_task_id": "provider-ordinary-1",
                "next_revision": 1,
            }
        ]
        evidence = [
            {
                "provider_id": "paisio",
                "provider_task_id": "provider-ordinary-1",
                "state": "completed",
                "actual_cost_status": "actual",
                "actual_cost_cny": 0.29,
                "evidence_source": "newapi_authenticated_video_task",
                "fetched_at": 1786422600,
            }
        ]

        requests = publisher.build_newapi_settlement_requests(pending, evidence)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["job_id"], pending[0]["job_id"])
        self.assertEqual(requests[0]["contract_version"], "xtai-video-billing-v2")
        self.assertEqual(requests[0]["actual_cost_cny_exact"], "0.290000")

    def test_newapi_pending_task_rejects_ambiguous_provider_ids(self):
        pending = [
            {
                "job_id": "task_" + "b" * 32,
                "provider_task_id": "duplicate-id",
                "next_revision": 1,
            }
        ]
        evidence = [
            {
                "provider_id": provider,
                "provider_task_id": "duplicate-id",
                "state": "completed",
                "actual_cost_status": "actual",
                "actual_cost_cny": 1,
                "evidence_source": "provider_account_ledger",
                "fetched_at": 1786422600,
            }
            for provider in ("paisio", "toonflow")
        ]

        self.assertEqual(
            publisher.build_newapi_settlement_requests(pending, evidence), []
        )


if __name__ == "__main__":
    unittest.main()
