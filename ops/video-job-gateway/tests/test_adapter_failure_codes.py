import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import _observation


class AdapterFailureCodeTests(unittest.TestCase):
    def test_credential_refresh_failure_receives_stable_infrastructure_code(self):
        observation = _observation(
            {
                "id": "provider-task-1",
                "status": "failed",
                "message": "refresh leased account credential: invalid Adobe refresh response",
            }
        )
        self.assertEqual(observation.status, "failed")
        self.assertEqual(observation.error_code, "provider_credential_refresh_failed")

    def test_reference_fetch_rejection_receives_non_infrastructure_code(self):
        observation = _observation(
            {
                "id": "provider-task-2",
                "status": "failed",
                "message": "Cannot fetch content from the provided URL",
            }
        )
        self.assertEqual(observation.status, "failed")
        self.assertEqual(observation.error_code, "video_reference_fetch_rejected")

    def test_scheduler_claim_timeout_receives_capacity_code(self):
        observation = _observation(
            {
                "status": "failed",
                "task_id": "provider-task-capacity",
                "message": "no eligible account: scheduler claim wait timed out",
            }
        )

        self.assertEqual(observation.error_code, "provider_capacity_exhausted")


if __name__ == "__main__":
    unittest.main()
