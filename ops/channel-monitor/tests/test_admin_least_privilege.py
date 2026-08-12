import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER = ROOT / "scripts" / "patch-admin-server-security.py"
NGINX_PATCHER = ROOT / "scripts" / "patch-nginx-admin-token.py"
COMPOSE_PATCHER = ROOT / "scripts" / "patch-compose-admin-token.py"
LEDGER_PATCHER = ROOT / "scripts" / "patch-ledger-private-mode.py"
UNIT = ROOT / "systemd" / "channel-monitor-admin.service"
REGENERATE_PATH = ROOT / "systemd" / "channel-monitor-regenerate.path"
REGENERATE_SERVICE = ROOT / "systemd" / "channel-monitor-regenerate.service"


def load_patcher():
    spec = importlib.util.spec_from_file_location("admin_security_patcher", PATCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_nginx_patcher():
    spec = importlib.util.spec_from_file_location("nginx_admin_patcher", NGINX_PATCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_compose_patcher():
    spec = importlib.util.spec_from_file_location("compose_admin_patcher", COMPOSE_PATCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_ledger_patcher():
    spec = importlib.util.spec_from_file_location("ledger_mode_patcher", LEDGER_PATCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdminLeastPrivilegeTests(unittest.TestCase):
    def fixture(self):
        return '''#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler

MAX_BODY = 1024 * 1024

def atomic_write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text('json', encoding='utf-8')
    tmp.replace(path)

def atomic_write_credentials(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text('json', encoding='utf-8')
    tmp.replace(path)

def normalize_list(value):
    return value

def mask_credentials(pwd):
    return {
        'password_preview': (pwd[:2] + '****') if pwd else '',
    }

def regenerate():
    result = subprocess.run(['docker', 'exec'], capture_output=True)
    return {'ok': result.returncode == 0}

class Handler(BaseHTTPRequestHandler):
    server_version = 'ChannelMonitorAdmin/1.0'

    def send_json(self, status, payload):
        pass

    def do_GET(self):
        path = self.path

    def do_POST(self):
        path = self.path

if __name__ == '__main__':
    bind_host = os.environ.get('CHANNEL_MONITOR_ADMIN_BIND_HOST', '127.0.0.1')
'''

    def test_patcher_requires_application_token_for_get_post_and_startup(self):
        patcher = load_patcher()
        patched = patcher.patch_source(self.fixture())

        self.assertIn("import hmac", patched)
        self.assertIn("CHANNEL_MONITOR_ADMIN_TOKEN", patched)
        self.assertEqual(patched.count("if not self.is_authorized():"), 2)
        self.assertIn("hmac.compare_digest", patched)
        self.assertIn("validate_admin_token()", patched)
        self.assertNotIn("pwd[:2]", patched)
        self.assertIn("'password_preview': '****' if pwd else ''", patched)
        self.assertIn("def _write_private_json(path, data):", patched)
        self.assertIn("threading.Lock()", patched)
        self.assertIn("os.fsync(handle.fileno())", patched)
        self.assertIn("f'.{path.name}.previous'", patched)
        self.assertIn(".admin-regenerate-request", patched)
        self.assertIn("'queued': True", patched)
        self.assertNotIn("subprocess.run(['docker', 'exec']", patched)
        self.assertEqual(patcher.patch_source(patched), patched)

    def test_systemd_unit_has_dedicated_user_and_strict_write_boundary(self):
        unit = UNIT.read_text(encoding="utf-8")

        required = [
            "User=channel-monitor-admin",
            "Group=channel-monitor-admin",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "CapabilityBoundingSet=",
            "UMask=0022",
            "ReadWritePaths=/opt/ai-api-stack/channel-monitor/data",
            "EnvironmentFile=/etc/channel-monitor-admin.env",
        ]
        for directive in required:
            with self.subTest(directive=directive):
                self.assertIn(directive, unit)
        self.assertNotIn("docker.sock", unit)

        path_unit = REGENERATE_PATH.read_text(encoding="utf-8")
        helper_unit = REGENERATE_SERVICE.read_text(encoding="utf-8")
        self.assertIn("PathChanged=/opt/ai-api-stack/channel-monitor/data/.admin-regenerate-request", path_unit)
        self.assertIn("User=channel-monitor-admin", helper_unit)
        self.assertIn("SupplementaryGroups=docker", helper_unit)
        self.assertIn("scripts/generate-monitor-data.py", helper_unit)
        self.assertNotIn("SupplementaryGroups=docker", unit)
        self.assertNotIn("ListenStream", helper_unit + path_unit)

    def test_nginx_patcher_injects_private_header_after_basic_auth(self):
        patcher = load_nginx_patcher()
        source = '''server {
    location /channel-monitor/admin/ {
        auth_basic "Channel Monitor";
        auth_basic_user_file /etc/nginx/auth/channel-monitor.htpasswd;
        proxy_pass http://172.18.0.1:8791/channel-monitor/admin/;
    }
}
'''

        patched = patcher.patch_source(source)

        self.assertIn("auth_basic_user_file", patched)
        self.assertIn(
            "include /etc/nginx/auth/channel-monitor-token.inc;", patched
        )
        self.assertLess(patched.index("auth_basic_user_file"), patched.index("include"))
        self.assertEqual(patcher.patch_source(patched), patched)

    def test_compose_patcher_mounts_token_include_read_only(self):
        patcher = load_compose_patcher()
        source = '''  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx/conf.d/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/certs:/etc/nginx/certs:ro
      - ./channel-monitor:/usr/share/nginx/html/channel-monitor:ro
      - ./nginx/auth/channel-monitor.htpasswd:/etc/nginx/auth/channel-monitor.htpasswd:ro
'''

        patched = patcher.patch_source(source)

        self.assertIn(
            "./nginx/auth/channel-monitor-token.inc:/etc/nginx/auth/channel-monitor-token.inc:ro",
            patched,
        )
        self.assertEqual(patcher.patch_source(patched), patched)

    def test_ledger_patcher_preserves_surrounding_production_source(self):
        patcher = load_ledger_patcher()
        source = '''def write_json(path, value):
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, path)

PRODUCTION_ONLY_SENTINEL = True
'''

        patched = patcher.patch_source(source)

        self.assertIn("os.chmod(temporary, 0o600)", patched)
        self.assertIn("os.chown(temporary, current.st_uid, current.st_gid)", patched)
        self.assertIn("PRODUCTION_ONLY_SENTINEL = True", patched)
        self.assertEqual(patcher.patch_source(patched), patched)


if __name__ == "__main__":
    unittest.main()
