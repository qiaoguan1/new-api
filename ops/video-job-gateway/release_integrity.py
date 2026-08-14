"""Fail-closed release identity checks for the video gateway image."""

from __future__ import annotations

import hashlib
import hmac
import pathlib
import re


FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
RUNTIME_RELEASE_FILES = (
    "adapters.py",
    "app.py",
    "billing_collectors.py",
    "catalog.json",
    "catalog.py",
    "credential_lifecycle.py",
    "reference_contract.py",
    "relay-pricing.json",
    "relay_pricing.py",
    "release_integrity.py",
    "routing.py",
    "store.py",
)


class ReleaseIntegrityError(RuntimeError):
    """Raised when a gateway image cannot be bound to exact source content."""


def verify_release_identity(
    vcs_ref: str,
    expected_catalog_sha256: str,
    catalog_path: str | pathlib.Path,
    expected_source_sha256: str,
    source_root: str | pathlib.Path,
) -> dict[str, str]:
    """Verify the commit, catalog, and complete runtime source copied into an image."""

    normalized_ref = str(vcs_ref or "").strip().lower()
    normalized_digest = str(expected_catalog_sha256 or "").strip().lower()
    normalized_source_digest = str(expected_source_sha256 or "").strip().lower()
    if not FULL_GIT_SHA.fullmatch(normalized_ref):
        raise ReleaseIntegrityError("XTAI_VCS_REF must be a full 40-character Git commit")
    if not SHA256_HEX.fullmatch(normalized_digest):
        raise ReleaseIntegrityError("XTAI_CATALOG_SHA256 must be a 64-character SHA-256 digest")
    if not SHA256_HEX.fullmatch(normalized_source_digest):
        raise ReleaseIntegrityError("XTAI_SOURCE_SHA256 must be a 64-character SHA-256 digest")

    path = pathlib.Path(catalog_path)
    if not path.is_file():
        raise ReleaseIntegrityError("gateway catalog is missing from the release image")
    actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual_digest, normalized_digest):
        raise ReleaseIntegrityError("gateway catalog digest does not match the declared release")

    actual_source_digest = gateway_source_sha256(source_root)
    if not hmac.compare_digest(actual_source_digest, normalized_source_digest):
        raise ReleaseIntegrityError("gateway runtime source digest does not match the declared release")
    return {
        "vcs_ref": normalized_ref,
        "catalog_sha256": actual_digest,
        "source_sha256": actual_source_digest,
    }


def gateway_source_sha256(source_root: str | pathlib.Path) -> str:
    """Return a deterministic digest of every file copied into the runtime image."""

    root = pathlib.Path(source_root)
    digest = hashlib.sha256()
    for name in RUNTIME_RELEASE_FILES:
        path = root / name
        if not path.is_file():
            raise ReleaseIntegrityError(f"gateway runtime source is missing required file: {name}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
