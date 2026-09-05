#!/usr/bin/env python3
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name('patrol_repair.py')
if not MODULE_PATH.exists():
    MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'patrol_repair.py'
SPEC = importlib.util.spec_from_file_location('patrol_repair_under_test', MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AuditArtifactTests(unittest.TestCase):
    def test_disabled_channels_do_not_create_false_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'audit.json'
            path.write_text(json.dumps({
                'date': '2026-09-05',
                'summary': {'failed_channels': 0},
                'channels': [
                    {'status': 1, 'scan_status': 'ok'},
                    {'status': 2, 'scan_status': 'skipped_disabled'},
                ],
            }), encoding='utf-8')
            item = {
                'id': 'artifact.daily_audit',
                'kind': 'artifact',
                'path': str(path),
                'artifact_type': 'audit',
                'severity': 'warning',
            }
            now = int(datetime.datetime(
                2026, 9, 6, 1, 0, tzinfo=MODULE.BEIJING
            ).timestamp())

            result = MODULE.PatrolChecks(MODULE.CommandRunner())._artifact(item, now)

            self.assertEqual('healthy', result.status)
            self.assertEqual('ok', result.code)


if __name__ == '__main__':
    unittest.main()
