import contextlib
import copy
import datetime
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import official_video_pricing as official


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


worker = load_module("official_video_worker_execution", "apply-official-video-pricing.py")
generic = load_module("official_execution_generic_helpers", "auto-apply-pricing.py")
DAY = "2026-10-09"
NOW = datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc)


class OfficialExecutionLoggingTests(unittest.TestCase):
    def fixture(self, root, *, changed=False, dry_run=False):
        catalog = {
            "schema_version": 1,
            "revision": "fixture-official-v1",
            "currency": "CNY",
            "markup": 1.5,
            "source_url": "https://www.volcengine.com/docs/fixture",
            "source_checked_at": "2026-10-09T00:00:00+00:00",
            "valid_until": "2026-10-20T00:00:00+00:00",
            "token_formula": copy.deepcopy(official.EXPECTED_FORMULA),
            "models": {
                model: {
                    "resolutions": sorted(resolutions),
                    "cny_per_m_tokens_by_resolution": {
                        resolution: {"no_video_input": 1, "with_video_input": 1}
                        for resolution in resolutions
                    },
                }
                for model, resolutions in official.EXPECTED_MODELS.items()
            },
        }
        routes = [{"raw_model": "sd3-720p", "stable_model": "seedance-2.0", "resolution": "720p"}]
        empty = {"ModelRatio": {}, "CompletionRatio": {}, "ModelPrice": {"unrelated": 2.0}, "GroupRatio": {"default": 0.15}}
        plan = official.build_official_model_price_plan(catalog, routes, empty)
        current = copy.deepcopy(plan["options"])
        current["GroupRatio"] = {"default": 0.15}
        if changed:
            current["ModelPrice"]["sd3-720p"] = 2.0
        actor = types.SimpleNamespace(
            OPTION_KEYS=generic.OPTION_KEYS,
            PricingError=generic.PricingError,
            target_beijing_day=mock.Mock(return_value=DAY),
            get_option=mock.Mock(side_effect=lambda key: copy.deepcopy(current[key])),
            backup_pricing_options=mock.Mock(return_value=root / "backups" / "fixture"),
            atomic_update_options=mock.Mock(return_value="COMMIT"),
            read_json=generic.read_json,
            write_json=generic.write_json,
        )
        documents = {
            root / "config" / "official-video-pricing.json": catalog,
            root / "config" / "video-model-policy.json": {},
            root / "data" / "video-model-mapping-report.json": {},
            root / "data" / "daily-upstream-audit.json": {"date": DAY},
        }
        for path, document in documents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document), encoding="utf-8")
        argv = ["--root", str(root)] + (["--dry-run"] if dry_run else [])
        return actor, current, plan, routes, argv

    def run_fixture(self, fixture, *, error=None):
        actor, current, plan, routes, argv = fixture
        stdout, stderr = io.StringIO(), io.StringIO()
        validation = (lambda value: official.validate_official_video_pricing(value, now=NOW)) if error is None else mock.Mock(side_effect=error)
        with (
            mock.patch.object(worker, "_load_generic_pricing_module", return_value=actor),
            mock.patch.object(worker, "resolve_beijing_business_day", return_value=DAY, create=True),
            mock.patch.object(worker, "validate_official_video_pricing", side_effect=validation),
            mock.patch.object(worker, "validate_policy", side_effect=lambda value: value),
            mock.patch.object(worker, "build_official_routes", return_value=routes),
            mock.patch.object(worker.time, "time", return_value=int(NOW.timestamp())),
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr),
        ):
            code = worker.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_unchanged_run_is_completed_without_fake_apply_or_database_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root)
            code, output, _ = self.run_fixture(fixture)
            actor, current, expected, _, _ = fixture
            self.assertEqual(code, 0)
            actor.atomic_update_options.assert_not_called()
            actor.backup_pricing_options.assert_not_called()
            run = json.loads(output)
            self.assertEqual(run["status"], "complete")
            self.assertIs(run["changed"], False)
            self.assertEqual({row["action"] for row in run["decisions"]}, {"unchanged"})
            self.assertEqual(run["applied"], 0)
            self.assertEqual(run["unchanged"], len(expected["decisions"]))
            saved = json.loads((root / "data" / "official-video-pricing-log.json").read_text())
            self.assertEqual(saved["runs"][-1], run)

    def test_changed_run_labels_only_changed_model_and_preserves_original_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root, changed=True)
            code, output, _ = self.run_fixture(fixture)
            actor, current, expected, _, _ = fixture
            self.assertEqual(code, 0)
            actor.atomic_update_options.assert_called_once_with(expected["options"], {key: current[key] for key in actor.OPTION_KEYS})
            run = json.loads(output)
            self.assertEqual(run["status"], "complete")
            applied = [row["model"] for row in run["decisions"] if row["action"] == "apply"]
            self.assertEqual(applied, ["sd3-720p"])
            self.assertEqual(run["applied"], 1)
            self.assertEqual(run["unchanged"], len(expected["decisions"])-1)

    def test_failed_run_is_dated_sanitized_and_preserves_prior_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root)
            path = root / "data" / "official-video-pricing-log.json"
            previous = {"date": "2026-10-08", "status": "complete", "changed": False}
            path.write_text(json.dumps({"runs": [previous], "retained_metadata": "keep"}))
            code, _, stderr = self.run_fixture(fixture, error=official.OfficialVideoPricingError("private-upstream-message"))
            actor = fixture[0]
            self.assertEqual(code, 1)
            actor.atomic_update_options.assert_not_called()
            history = json.loads(path.read_text())
            self.assertEqual(history["runs"][0], previous)
            self.assertEqual(history["retained_metadata"], "keep")
            failure = history["runs"][-1]
            self.assertEqual((failure["date"], failure["status"]), (DAY, "failed"))
            self.assertTrue(failure["error"])
            self.assertNotIn("private-upstream-message", json.dumps(history) + stderr)

    def test_dry_run_has_no_actual_write_count_or_database_update(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(pathlib.Path(directory), changed=True, dry_run=True)
            code, output, _ = self.run_fixture(fixture)
            self.assertEqual(code, 0)
            fixture[0].atomic_update_options.assert_not_called()
            fixture[0].backup_pricing_options.assert_not_called()
            self.assertEqual(json.loads(output)["applied"], 0)

    def test_uncertain_database_write_is_not_reported_as_zero_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root, changed=True)
            fixture[0].atomic_update_options.side_effect = generic.PricingError("private-db-detail")
            code, _, stderr = self.run_fixture(fixture)
            self.assertEqual(code, 1)
            run = json.loads((root / "data" / "official-video-pricing-log.json").read_text())["runs"][-1]
            self.assertTrue(run["database_write_attempted"])
            self.assertIsNone(run["applied"])
            self.assertNotIn("private-db-detail", stderr)

    def test_corrupt_history_is_preserved_before_any_database_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root, changed=True)
            path = root / "data" / "official-video-pricing-log.json"
            path.write_bytes(b"existing invalid audit history")
            code, _, _ = self.run_fixture(fixture)
            self.assertEqual(code, 1)
            fixture[0].backup_pricing_options.assert_not_called()
            fixture[0].atomic_update_options.assert_not_called()
            self.assertEqual(path.read_bytes(), b"existing invalid audit history")

    def test_unexpected_validation_exception_still_creates_sanitized_dated_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root)
            code, _, stderr = self.run_fixture(fixture, error=TypeError("private-runtime-detail"))
            self.assertEqual(code, 1)
            run = json.loads((root / "data" / "official-video-pricing-log.json").read_text())["runs"][-1]
            self.assertEqual((run["date"], run["status"]), (DAY, "failed"))
            self.assertNotIn("private-runtime-detail", stderr)

    def test_shared_module_load_failure_is_dated_sanitized_without_price_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = self.fixture(root)
            previous = {"date": "2026-10-08", "status": "complete", "changed": False}
            path = root / "data" / "official-video-pricing-log.json"
            path.write_text(json.dumps({"runs": [previous]}))
            stdout, stderr = io.StringIO(), io.StringIO()
            with (mock.patch.object(worker, "_load_generic_pricing_module", side_effect=ImportError("private-load-detail")),
                  mock.patch.object(worker, "resolve_beijing_business_day", return_value=DAY, create=True),
                  mock.patch.object(worker.time, "time", return_value=int(NOW.timestamp())),
                  contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
                code = worker.main(fixture[-1])
            self.assertEqual(code, 1)
            fixture[0].get_option.assert_not_called()
            fixture[0].atomic_update_options.assert_not_called()
            history = json.loads(path.read_text())
            self.assertEqual(history["runs"][0], previous)
            self.assertEqual((history["runs"][-1]["date"], history["runs"][-1]["status"]), (DAY, "failed"))
            self.assertNotIn("private-load-detail", stderr.getvalue()+json.dumps(history))


if __name__ == "__main__":
    unittest.main()
