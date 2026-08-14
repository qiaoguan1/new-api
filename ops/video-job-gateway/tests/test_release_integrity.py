import hashlib
import pathlib
import tempfile
import unittest

from release_integrity import (
    RUNTIME_RELEASE_FILES,
    ReleaseIntegrityError,
    gateway_source_sha256,
    verify_release_identity,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReleaseIntegrityTests(unittest.TestCase):
    def test_accepts_full_commit_and_matching_catalog_and_source_digests(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = pathlib.Path(directory)
            for name in RUNTIME_RELEASE_FILES:
                (root / name).write_bytes(f"fixture:{name}".encode("utf-8"))
            catalog = root / "catalog.json"
            catalog.write_bytes(b'{"revision":"test"}\n')
            digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
            source_digest = gateway_source_sha256(root)

            identity = verify_release_identity("a" * 40, digest, catalog, source_digest, root)

            self.assertEqual(identity["vcs_ref"], "a" * 40)
            self.assertEqual(identity["catalog_sha256"], digest)
            self.assertEqual(identity["source_sha256"], source_digest)

    def test_rejects_missing_or_mismatched_build_identity(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = pathlib.Path(directory)
            for name in RUNTIME_RELEASE_FILES:
                (root / name).write_bytes(f"fixture:{name}".encode("utf-8"))
            catalog = root / "catalog.json"
            catalog.write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
            source_digest = gateway_source_sha256(root)
            cases = (
                ("", digest, source_digest),
                ("short", digest, source_digest),
                ("b" * 40, "0" * 64, source_digest),
                ("b" * 40, digest, "0" * 64),
            )
            for vcs_ref, expected, expected_source in cases:
                with self.subTest(vcs_ref=vcs_ref, expected=expected, expected_source=expected_source):
                    with self.assertRaises(ReleaseIntegrityError):
                        verify_release_identity(vcs_ref, expected, catalog, expected_source, root)

    def test_dockerfile_enforces_and_labels_release_identity(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG XTAI_VCS_REF", dockerfile)
        self.assertIn("ARG XTAI_CATALOG_SHA256", dockerfile)
        self.assertIn("ARG XTAI_SOURCE_SHA256", dockerfile)
        self.assertIn("verify_release_identity", dockerfile)
        self.assertIn("org.opencontainers.image.revision", dockerfile)
        self.assertIn("com.aixingtuyun.video.catalog-sha256", dockerfile)
        self.assertIn("com.aixingtuyun.video.source-sha256", dockerfile)


if __name__ == "__main__":
    unittest.main()
