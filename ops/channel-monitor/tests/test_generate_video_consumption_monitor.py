import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generator = load_script("generate_video_consumption_monitor", "generate-video-consumption-monitor.py")
patcher = load_script("patch_generate_video_consumption", "patch_generate_video_consumption.py")


class VideoConsumptionGeneratorTests(unittest.TestCase):
    def test_build_uses_private_evidence_and_public_model_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            ledger = root / "ledger.json"
            database = root / "jobs.sqlite3"
            ledger.write_text(
                json.dumps(
                    {
                        "days": {
                            "2026-08-09": {
                                "toonflow": {
                                    "collection_status": "complete",
                                    "actual_log_complete": True,
                                    "fetched_at": 300,
                                    "day_log_cost_cny": 2.0,
                                    "video_task_evidence": [
                                        {
                                            "provider_id": "toonflow",
                                            "provider_task_id": "tf-1",
                                            "state": "completed",
                                            "created_at_epoch": 1786291100,
                                            "actual_cost_cny": 1.25,
                                            "actual_cost_status": "actual",
                                            "evidence_source": "toonflow_web_operation_log",
                                            "fetched_at": 300,
                                        }
                                    ],
                                },
                                "paisio": {
                                    "collection_status": "incomplete",
                                    "actual_log_complete": False,
                                    "day_log_cost_cny": None,
                                    "video_task_evidence": [],
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            connection = sqlite3.connect(database)
            connection.execute(
                """create table video_jobs(
                job_id text, provider_id text, upstream_task_id text, stable_model text,
                status text, payload_json text, created_at integer, updated_at integer,
                finished_at integer)"""
            )
            connection.execute(
                "insert into video_jobs values(?,?,?,?,?,?,?,?,?)",
                (
                    "job-1",
                    "toonflow",
                    "tf-1",
                    "seedance-2.0-full",
                    "succeeded",
                    json.dumps({"resolution": "720p", "_relay_price": {"amount_cny_exact": "4.5"}}),
                    1786291100,
                    1786291101,
                    1786291102,
                ),
            )
            connection.commit()
            connection.close()
            original = generator.LEDGER_PATH
            try:
                generator.LEDGER_PATH = ledger
                snapshots = generator.build("2026-08-09", gateway_db=database)
            finally:
                generator.LEDGER_PATH = original

        providers = {row["provider_id"]: row for row in snapshots["private"]["providers"]}
        self.assertEqual(providers["toonflow"]["provider_evidence_count"], 1)
        self.assertEqual(providers["toonflow"]["matched_actual_cost_cny"], 1.25)
        self.assertEqual(providers["toonflow"]["upstream_actual_cost_cny"], 2.0)
        self.assertEqual(providers["paisio"]["collection_status"], "incomplete")
        self.assertEqual(snapshots["public"]["models"][0]["model"], "seedance-2.0-full")
        self.assertNotIn("toonflow", json.dumps(snapshots["public"]))

    def test_generator_patch_is_idempotent_and_uses_existing_loader(self):
        source = '''ROOT = Path("/tmp")
def load_json(path, default):
    return default
def update_daily_history(payload):
    return []
def build():
    payload = {}
    payload["daily_history"] = update_daily_history(payload)
    return payload
'''

        once = patcher.patch_text(source)
        twice = patcher.patch_text(once)

        self.assertEqual(once, twice)
        self.assertEqual(once.count(patcher.MARKER), 1)
        compile(once, "generate-monitor-data.py", "exec")


if __name__ == "__main__":
    unittest.main()
