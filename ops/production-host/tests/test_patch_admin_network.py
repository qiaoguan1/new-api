import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "patch_admin_network.py"
SPEC = importlib.util.spec_from_file_location("patch_admin_network", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PatchAdminNetworkTests(unittest.TestCase):
    def test_patches_admin_server_to_secure_environment_bind(self) -> None:
        source = """#!/usr/bin/env python3
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', 8791), Handler)
    print('channel monitor admin listening on :8791')
    server.serve_forever()
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin-server.py"
            path.write_text(source, encoding="utf-8")
            MODULE.patch_admin_server(path)
            patched = path.read_text(encoding="utf-8")

        self.assertIn("import os", patched)
        self.assertIn("CHANNEL_MONITOR_ADMIN_BIND_HOST", patched)
        self.assertIn("127.0.0.1", patched)
        self.assertNotIn("('0.0.0.0', 8791)", patched)

    def test_patches_nginx_upstream_to_docker_bridge(self) -> None:
        source = "proxy_pass http://154.12.55.120:8791/channel-monitor/admin/;\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default.conf"
            path.write_text(source, encoding="utf-8")
            MODULE.patch_nginx(path)
            patched = path.read_text(encoding="utf-8")

        self.assertEqual(
            patched,
            "proxy_pass http://172.18.0.1:8791/channel-monitor/admin/;\n",
        )

    def test_second_patch_is_idempotent(self) -> None:
        source = """import json
import subprocess
if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', 8791), Handler)
    print('channel monitor admin listening on :8791')
    server.serve_forever()
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin-server.py"
            path.write_text(source, encoding="utf-8")
            MODULE.patch_admin_server(path)
            first = path.read_bytes()
            MODULE.patch_admin_server(path)
            second = path.read_bytes()

        self.assertEqual(first, second)

    def test_refuses_unknown_admin_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin-server.py"
            path.write_text("print('unexpected')\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected admin server anchors"):
                MODULE.patch_admin_server(path)


if __name__ == "__main__":
    unittest.main()
