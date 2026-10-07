"""Only evidenced exact image-mode tuples may be quoted or advertised."""

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nody_image_contracts import NodyImageContracts


def profile():
    return {"model": "grok-imagine-video-official", "mode": "reference", "image_count": 1,
            "resolution": "480p", "duration": 1, "actual_cost_cny_exact": "0.300000",
            "evidence_task_id": "f38d6547-27af-4f3b-8945-8c643162c432",
            "evidence_source": "nodyhub_authenticated_video_task", "status": "succeeded", "cost_status": "actual"}


class NodyImageProfileTests(unittest.TestCase):
    def load(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "profiles.json"
            path.write_text(json.dumps({"schema_version": "xtai-nody-image-input-v1", "revision": "test-evidence", "profiles": rows}))
            return NodyImageContracts.load(path)

    def test_only_exact_evidenced_mode_count_is_quoted(self):
        contracts = self.load([profile()])
        quote = contracts.quote("grok-imagine-video-official", "480p", 1, "reference", 1)
        self.assertEqual(quote["amount_cny_exact"], "0.450000")
        self.assertEqual(quote["reference_cost_cny_exact"], "0.300000")
        public = contracts.public_rows()
        self.assertEqual(public[0]["amount_cny_exact"], "0.450000")
        self.assertNotIn("actual_cost_cny_exact", public[0])
        self.assertNotIn("evidence_task_id", public[0])
        for mode, count in [("all_reference", 2), ("first_last_frame", 2), ("reference", 7)]:
            with self.assertRaises(ValueError):
                contracts.quote("grok-imagine-video-official", "480p", 1, mode, count)

    def test_missing_evidence_or_unknown_contract_is_rejected(self):
        for change in [{"status": "running"}, {"cost_status": "estimated"}, {"evidence_task_id": ""},
                       {"actual_cost_cny_exact": "NaN"}, {"actual_cost_cny_exact": "-1"},
                       {"duration": True}, {"image_count": True}, {"model": "omni-flash"},
                       {"mode": "first_last_frame"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([{**profile(), **change}])
        with self.assertRaises(ValueError):
            self.load([profile(), profile()])

    def test_no_config_keeps_new_modes_unavailable(self):
        contracts = NodyImageContracts.load(None)
        self.assertEqual(contracts.public_rows(), [])
        with self.assertRaises(ValueError):
            contracts.quote("grok-imagine-video-official", "480p", 1, "reference", 1)


if __name__ == "__main__":
    unittest.main()
