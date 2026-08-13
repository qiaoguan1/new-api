import base64
import json
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from credential_lifecycle import (
    CredentialLifecycleError,
    atomic_install_captcha_token,
    inspect_captcha_token,
    warning_event,
)


def jwt(payload):
    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return ".".join(
        (
            encode(b'{"alg":"HS256","typ":"JWT"}'),
            encode(json.dumps(payload, separators=(",", ":")).encode()),
            encode(b"signature-placeholder"),
        )
    )


class CredentialLifecycleTests(unittest.TestCase):
    def test_captcha_bound_token_reports_expiry_without_exposing_value(self):
        token = jwt({"iss": "toonflow", "aud": "console", "exp": 2_000_086_400})
        status = inspect_captcha_token(
            token,
            provider_id="toonflow",
            now=2_000_000_000,
            expected_issuer="toonflow",
            expected_audience="console",
        )
        self.assertTrue(status.ready)
        self.assertEqual(status.refresh_mode, "captcha_bound")
        self.assertEqual(status.remaining_seconds, 86_400)
        self.assertNotIn(token, repr(status))

    def test_wrong_identity_and_expired_token_fail_closed(self):
        with self.assertRaises(CredentialLifecycleError) as wrong:
            inspect_captcha_token(
                jwt({"iss": "attacker", "aud": "console", "exp": 2_000_086_400}),
                provider_id="toonflow",
                now=2_000_000_000,
                expected_issuer="toonflow",
                expected_audience="console",
            )
        self.assertEqual(wrong.exception.code, "credential_issuer_invalid")

        status = inspect_captcha_token(
            jwt({"iss": "toonflow", "aud": "console", "exp": 1_999_999_999}),
            provider_id="toonflow",
            now=2_000_000_000,
        )
        self.assertFalse(status.ready)
        self.assertEqual(status.state, "expired")

    def test_atomic_install_preserves_longer_current_token_and_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = pathlib.Path(directory) / "toonflow.token"
            current = jwt({"iss": "toonflow", "aud": "console", "exp": 2_000_200_000})
            shorter = jwt({"iss": "toonflow", "aud": "console", "exp": 2_000_100_000})
            path.write_text(current, encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o600)

            with self.assertRaises(CredentialLifecycleError) as failure:
                atomic_install_captcha_token(
                    path,
                    shorter,
                    provider_id="toonflow",
                    now=2_000_000_000,
                    expected_issuer="toonflow",
                    expected_audience="console",
                )
            self.assertEqual(failure.exception.code, "credential_lifetime_regression")
            self.assertEqual(path.read_text(encoding="utf-8"), current)

            newer = jwt({"iss": "toonflow", "aud": "console", "exp": 2_003_000_000})
            atomic_install_captcha_token(
                path,
                newer,
                provider_id="toonflow",
                now=2_000_000_000,
                expected_issuer="toonflow",
                expected_audience="console",
            )
            self.assertEqual(path.read_text(encoding="utf-8"), newer)
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_atomic_install_rejects_existing_symlink(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = pathlib.Path(directory)
            target = root / "real.token"
            link = root / "toonflow.token"
            current = jwt({"iss": "toonflow", "exp": 2_000_200_000})
            target.write_text(current, encoding="utf-8")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            replacement = jwt({"iss": "toonflow", "exp": 2_003_000_000})

            with self.assertRaises(CredentialLifecycleError) as failure:
                atomic_install_captcha_token(
                    link,
                    replacement,
                    provider_id="toonflow",
                    now=2_000_000_000,
                )

            self.assertEqual(failure.exception.code, "credential_file_unsafe")
            self.assertEqual(target.read_text(encoding="utf-8"), current)

    def test_warning_thresholds_are_deduplicated(self):
        status = inspect_captcha_token(
            jwt({"iss": "toonflow", "exp": 2_000_250_000}),
            provider_id="toonflow",
            now=2_000_000_000,
        )
        state = {}
        first = warning_event(status, state, thresholds_days=(30, 14, 7, 3, 1), now=2_000_000_000)
        second = warning_event(status, state, thresholds_days=(30, 14, 7, 3, 1), now=2_000_000_001)
        self.assertEqual(first["kind"], "credential_expiring")
        self.assertIsNone(second)
        self.assertEqual(state["toonflow"]["last_threshold_days"], 3)


if __name__ == "__main__":
    unittest.main()
