"""Prevent duplicate paid submissions and reuse of overbroad probe credentials."""

from pathlib import Path
import tempfile
import time
import unittest

from probe import reserve_marker, validate_probe_token


class ProbeBoundaryTests(unittest.TestCase):
    def test_started_marker_cannot_be_overwritten_for_second_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "probe-started.json"
            reserve_marker(target, {"submitted": False})
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                reserve_marker(target, {"submitted": True})
            self.assertEqual(target.read_bytes(), before)

    def test_finite_model_ip_and_time_scoped_token_is_accepted(self) -> None:
        token = self.token()
        validate_probe_token(token, "ccmax", 1.0)

    def token(self) -> dict:
        return {"group": "ccmax", "status": 1, "unlimited_quota": False,
                "model_limits_enabled": True, "model_limits": "claude-fable-5-1",
                "allow_ips": "156.239.3.210", "remain_quota": 500000,
                "expired_time": int(time.time()) + 3600, "cross_group_retry": False}

    def test_rejects_over_budget_expired_and_cross_group_credentials(self) -> None:
        for field, value in [("remain_quota", 500001), ("expired_time", -1),
                             ("expired_time", int(time.time()) - 1),
                             ("cross_group_retry", True), ("unlimited_quota", True),
                             ("allow_ips", "")]:
            token = self.token()
            token[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_probe_token(token, "ccmax", 1.0)


if __name__ == "__main__":
    unittest.main()
