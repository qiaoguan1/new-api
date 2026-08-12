#!/usr/bin/env python3
"""Idempotently add trusted-proxy authentication to the production admin server."""

import argparse
from pathlib import Path


MARKER = "CHANNEL_MONITOR_ADMIN_TOKEN"
PASSWORD_PREVIEW = "'password_preview': (pwd[:2] + '****') if pwd else '',"
SAFE_PASSWORD_PREVIEW = "'password_preview': '****' if pwd else '',"


class PatchError(RuntimeError):
    """Raised when the target does not match the reviewed production source."""


def replace_once(source: str, old: str, new: str) -> str:
    """Replace one reviewed anchor or fail closed on source drift."""
    count = source.count(old)
    if count != 1:
        raise PatchError(f"expected one reviewed anchor, found {count}: {old!r}")
    return source.replace(old, new, 1)


def patch_regenerate(source: str) -> str:
    """Replace direct Docker-backed regeneration with a private queued request."""
    if ".admin-regenerate-request" in source and "'queued': True" in source:
        return source
    start_anchor = "def regenerate():\n"
    end_anchor = "\n\nclass Handler(BaseHTTPRequestHandler):"
    if source.count(start_anchor) != 1 or source.count(end_anchor) != 1:
        raise PatchError("expected one reviewed regenerate function")
    start = source.index(start_anchor)
    end = source.index(end_anchor, start)
    replacement = '''def regenerate():
    """Queue immediate regeneration for the isolated root oneshot helper."""
    started = time.time()
    request = DATA_ROOT / '.admin-regenerate-request'
    temporary = request.with_suffix('.tmp')
    temporary.write_text(str(time.time()), encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(request)
    return {
        'ok': True,
        'queued': True,
        'duration_seconds': round(time.time() - started, 3),
    }
'''
    return source[:start] + replacement + source[end:]


def patch_private_writers(source: str) -> str:
    """Keep config writes inside existing files without parent-directory access."""
    if "def _write_private_json(path, data):" in source:
        return source
    start_anchor = "def atomic_write_json(path, data):\n"
    end_anchor = "\n\ndef normalize_list(value):"
    if source.count(start_anchor) != 1 or source.count(end_anchor) != 1:
        raise PatchError("expected the reviewed admin JSON writers")
    start = source.index(start_anchor)
    end = source.index(end_anchor, start)
    replacement = '''_WRITE_LOCK = threading.Lock()


def _write_private_json(path, data):
    """Serialize, fsync, verify and restore a private existing config file."""
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + '\\n'
    with _WRITE_LOCK:
        original = path.read_text(encoding='utf-8')
        recovery = DATA_ROOT / f'.{path.name}.previous'
        recovery.write_text(original, encoding='utf-8')
        os.chmod(recovery, 0o600)
        try:
            with path.open('r+', encoding='utf-8') as handle:
                handle.seek(0)
                handle.write(encoded)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
            json.loads(path.read_text(encoding='utf-8'))
        except BaseException:
            with path.open('r+', encoding='utf-8') as handle:
                handle.seek(0)
                handle.write(original)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            raise


def atomic_write_json(path, data):
    _write_private_json(path, data)


def atomic_write_credentials(path, data):
    _write_private_json(path, data)
'''
    return source[:start] + replacement + source[end:]


def patch_source(source: str) -> str:
    """Return the authenticated admin source without changing unrelated behavior."""
    if MARKER in source and "hmac.compare_digest" in source:
        patched = source.replace(PASSWORD_PREVIEW, SAFE_PASSWORD_PREVIEW)
        return patch_regenerate(patch_private_writers(patched))

    source = replace_once(
        source, "import json\n", "import hmac\nimport json\nimport threading\n"
    )
    source = replace_once(
        source,
        "MAX_BODY = 1024 * 1024\n",
        '''MAX_BODY = 1024 * 1024
ADMIN_TOKEN_ENV = 'CHANNEL_MONITOR_ADMIN_TOKEN'
ADMIN_TOKEN_HEADER = 'X-Channel-Monitor-Admin-Token'


def validate_admin_token():
    """Fail startup closed when the reverse-proxy shared secret is unavailable."""
    token = os.environ.get(ADMIN_TOKEN_ENV, '')
    if len(token) < 32 or len(token) > 256:
        raise RuntimeError(f'{ADMIN_TOKEN_ENV} must contain 32-256 characters')
    return token
''',
    )
    source = replace_once(
        source,
        "class Handler(BaseHTTPRequestHandler):\n    server_version = 'ChannelMonitorAdmin/1.0'\n",
        '''class Handler(BaseHTTPRequestHandler):
    server_version = 'ChannelMonitorAdmin/1.0'

    def is_authorized(self):
        """Accept only the bounded secret injected by the trusted reverse proxy."""
        expected = os.environ.get(ADMIN_TOKEN_ENV, '')
        supplied = self.headers.get(ADMIN_TOKEN_HEADER, '')
        return (
            32 <= len(expected) <= 256
            and len(supplied) <= 256
            and hmac.compare_digest(supplied, expected)
        )
''',
    )
    source = replace_once(
        source,
        "    def do_GET(self):\n",
        "    def do_GET(self):\n        if not self.is_authorized():\n            return self.send_json(403, {'success': False, 'message': 'forbidden'})\n",
    )
    source = replace_once(
        source,
        "    def do_POST(self):\n",
        "    def do_POST(self):\n        if not self.is_authorized():\n            return self.send_json(403, {'success': False, 'message': 'forbidden'})\n",
    )
    source = replace_once(
        source,
        "if __name__ == '__main__':\n    bind_host = ",
        "if __name__ == '__main__':\n    validate_admin_token()\n    bind_host = ",
    )
    source = source.replace(PASSWORD_PREVIEW, SAFE_PASSWORD_PREVIEW)
    return patch_regenerate(patch_private_writers(source))


def main(argv=None) -> int:
    """Patch a file in place after validating every reviewed anchor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    original = args.path.read_text(encoding="utf-8")
    patched = patch_source(original)
    if patched != original:
        temporary = args.path.with_suffix(args.path.suffix + ".issue24.tmp")
        temporary.write_text(patched, encoding="utf-8")
        temporary.chmod(args.path.stat().st_mode & 0o777)
        temporary.replace(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
