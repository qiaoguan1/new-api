import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PatrolSystemdTests(unittest.TestCase):
    def read(self, name):
        return (ROOT / "systemd" / name).read_text(encoding="utf-8")

    def test_api_is_unprivileged_loopback_and_uses_systemd_credential(self):
        unit = self.read("channel-monitor-patrol-api.service")
        self.assertIn("User=channel-monitor-patrol-api", unit)
        self.assertIn("LoadCredential=api_token:/etc/channel-monitor-patrol-api.token", unit)
        self.assertIn("/usr/local/lib/channel-monitor-patrol/patrol-repair-api.py", unit)
        self.assertIn("--host 127.0.0.1", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ReadWritePaths=/run/channel-monitor-patrol-trigger", unit)
        self.assertNotIn("SupplementaryGroups=docker", unit)

    def test_root_worker_has_no_listener_and_uses_fixed_flock(self):
        unit = self.read("channel-monitor-patrol-repair.service")
        self.assertIn("Type=oneshot", unit)
        self.assertIn("/usr/bin/flock -n /run/lock/channel-monitor-patrol-repair.lock", unit)
        self.assertIn("EnvironmentFile=/opt/ai-api-stack/channel-monitor/balance-alert.env", unit)
        self.assertIn("SuccessExitStatus=2", unit)
        self.assertNotIn("--host", unit)
        self.assertNotIn("ListenStream", unit)

    def test_path_and_beijing_timer_trigger_the_same_worker(self):
        path = self.read("channel-monitor-patrol-repair.path")
        timer = self.read("channel-monitor-patrol-repair.timer")
        self.assertIn("PathChanged=/run/channel-monitor-patrol-trigger/run.request", path)
        self.assertIn("Unit=channel-monitor-patrol-repair.service", path)
        self.assertIn("OnCalendar=*-*-* 09:15:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=channel-monitor-patrol-repair.service", timer)

    def test_tmpfiles_keep_status_root_owned_and_trigger_api_owned(self):
        config = (ROOT / "tmpfiles.d" / "channel-monitor-patrol.conf").read_text(encoding="utf-8")
        self.assertIn("/var/lib/channel-monitor-patrol 2750 root channel-monitor-patrol-api", config)
        self.assertIn("/run/channel-monitor-patrol-trigger 0700 channel-monitor-patrol-api channel-monitor-patrol-api", config)


if __name__ == "__main__":
    unittest.main()
