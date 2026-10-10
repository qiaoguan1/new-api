"""Free rollout guard tests; no production or HTTP interaction."""
import importlib.util
import copy
import hashlib
import json
import pathlib
import sys
import os
import types
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("adapter_promote", HERE / "promote_adapters.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class PromotionGuards(unittest.TestCase):
    def test_quiet_requires_three_real_samples_without_resetting_short_history(self):
        sample = {"image_jobs_active": 0, "banana_active_requests": 0, "nody_handler_threads": 1,
                  "image25_handler_threads": 1, "native_reading": 0, "native_writing": 1,
                  "admission_verified": True, "channel_tests_disabled": True}
        with tempfile.TemporaryDirectory() as directory:
            drain = pathlib.Path(directory) / "DRAIN"
            drain.write_bytes(p.MARKER)
            with patch.object(p, "DRAIN", drain), patch.object(p, "idle_sample", return_value=sample) as check, patch.object(p.time, "sleep"):
                result = p.assert_quiet("g" * 64)
        self.assertEqual(len(result["samples"]), 3)
        self.assertEqual(check.call_count, 3)

    def test_real_inflight_resets_three_sample_window(self):
        sample = {"image_jobs_active": 0, "banana_active_requests": 0, "nody_handler_threads": 1,
                  "image25_handler_threads": 1, "native_reading": 0, "native_writing": 1,
                  "admission_verified": True, "channel_tests_disabled": True}
        busy = dict(sample, nody_handler_threads=2)
        with tempfile.TemporaryDirectory() as directory:
            drain = pathlib.Path(directory) / "DRAIN"
            drain.write_bytes(p.MARKER)
            with patch.object(p, "DRAIN", drain), patch.object(p, "idle_sample", side_effect=[sample, busy, sample, sample, sample]) as check, patch.object(p.time, "sleep"):
                result = p.assert_quiet("g" * 64)
        self.assertEqual(check.call_count, 5)
        self.assertEqual(len(result["samples"]), 3)

    def test_external_nginx_change_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = pathlib.Path(directory) / "nginx.conf"
            conf.write_bytes(b"external current change")
            with patch.object(p, "CONF", conf), patch.object(p, "command") as command:
                with self.assertRaises(p.h.DeploymentError):
                    p.write_conf(b"ours", b"expected old")
                self.assertEqual(conf.read_bytes(), b"external current change")
                command.assert_not_called()

    def test_monitor_source_default_and_env_precedence_are_exact(self):
        empty = {"options": [], "channel_test_active": 0}
        self.assertFalse(p.channel_tests_enabled({}, empty))
        self.assertTrue(p.channel_tests_enabled({"CHANNEL_TEST_FREQUENCY": "10"}, empty))
        self.assertFalse(p.channel_tests_enabled({"CHANNEL_TEST_FREQUENCY": "10", "CHANNEL_TEST_ENABLED": "false"}, empty))
        configured = {"options": [{"key": "monitor_setting", "value": json.dumps({"auto_test_channel_enabled": True})}], "channel_test_active": 0}
        self.assertTrue(p.channel_tests_enabled({}, configured))
        self.assertFalse(p.channel_tests_enabled({"CHANNEL_TEST_ENABLED": "0"}, configured))

    def test_verified_legacy_profile_does_not_invent_enabled_env_override(self):
        empty = {"options": [], "channel_test_active": 0}
        self.assertFalse(p.channel_tests_enabled({}, empty, profile="legacy"))
        self.assertTrue(p.channel_tests_enabled({"CHANNEL_TEST_FREQUENCY": "10", "CHANNEL_TEST_ENABLED": "false"}, empty, profile="legacy"))
        self.assertTrue(p.channel_tests_enabled({"CHANNEL_TEST_FREQUENCY": "10", "CHANNEL_TEST_ENABLED": "unrecognized-by-legacy"}, empty, profile="legacy"))
        self.assertFalse(p.channel_tests_enabled({"CHANNEL_TEST_ENABLED": "true"}, empty, profile="legacy"))
        configured = {"options": [{"key": "monitor_setting.auto_test_channel_enabled", "value": "true"}], "channel_test_active": 0}
        self.assertTrue(p.channel_tests_enabled({"CHANNEL_TEST_ENABLED": "false"}, configured, profile="legacy"))
        self.assertFalse(p.channel_tests_enabled({"CHANNEL_TEST_FREQUENCY": "10", "CHANNEL_TEST_ENABLED": "false"}, empty, profile="modern"))

    def test_monitor_profile_is_bound_to_verified_source_hash_not_operator_choice(self):
        source = unittest.mock.Mock()
        source.is_file.return_value = True; source.is_symlink.return_value = False
        source.read_bytes.return_value = b"captured exact source"
        for digest, expected in ((p.MONITOR_SOURCE_SHA256, "modern"), ("8856093e690a099ebda31f0ef6e8d216bfe4815526a8ed6158752546e962ea5f", "legacy")):
            with patch.object(p, "MONITOR_SOURCE", source), patch.object(p.hashlib, "sha256", return_value=types.SimpleNamespace(hexdigest=lambda: digest)):
                self.assertEqual(p.monitor_implementation_verified(), expected)
        with patch.object(p, "MONITOR_SOURCE", source), patch.object(p.hashlib, "sha256", return_value=types.SimpleNamespace(hexdigest=lambda: "f" * 64)), self.assertRaises(p.h.DeploymentError):
            p.monitor_implementation_verified()
        with self.assertRaises(p.h.DeploymentError):
            p.channel_tests_enabled({}, {"options": [], "channel_test_active": 0}, profile="unknown")

    def test_root_directory_must_be_private_and_owned(self):
        root = unittest.mock.Mock()
        root.is_dir.return_value = True; root.is_symlink.return_value = False
        root.resolve.return_value = root; root.absolute.return_value = root
        for owner, mode in ((0, 0o40700), (10002, 0o40700), (0, 0o40755)):
            root.stat.return_value = types.SimpleNamespace(st_uid=owner, st_mode=mode)
            with patch.object(p, "ROOT", root):
                if owner == 0 and mode == 0o40700: p.assert_private_root()
                else:
                    with self.assertRaises(p.h.DeploymentError): p.assert_private_root()

    def test_journal_fsyncs_its_directory_after_atomic_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory); original_open = os.open; original_close = os.close
            def opened(path, flags, *args):
                return 999999 if path == root else original_open(path, flags, *args)
            def closed(fd):
                if fd != 999999: original_close(fd)
            with patch.object(p, "ROOT", root), patch.object(p, "STATE", root / "state.json"), patch.object(p.os, "O_NOFOLLOW", 0, create=True), patch.object(p.os, "open", side_effect=opened), patch.object(p.os, "close", side_effect=closed), patch.object(p.os, "fsync") as fsync:
                p.save({"phase": "safe-intent"})
            fsync.assert_any_call(999999)
            self.assertEqual(json.loads((root / "state.json").read_text()), {"phase": "safe-intent"})

    def test_optional_backup_never_runs_without_explicit_manifest_approval(self):
        with patch.object(p, "command") as command:
            p.pre_switch_backup({}, {})
            command.assert_not_called()
            with self.assertRaises(p.h.DeploymentError):
                p.pre_switch_backup({}, {"pre_switch_backup": {"enabled": False, "entrypoint_sha256": "a" * 64}})

    def test_conflicting_or_malformed_monitor_sources_are_not_guessed(self):
        for snapshot in (
            {"options": [{"key": "monitor_setting.auto_test_channel_enabled", "value": "false"}, {"key": "monitor_setting", "value": '{"auto_test_channel_enabled":true}'}], "channel_test_active": 0},
            {"options": [{"key": "monitor_setting", "value": "not-json"}], "channel_test_active": 0},
            {"options": [], "channel_test_active": None},
        ):
            with self.subTest(snapshot=snapshot), self.assertRaises(p.h.DeploymentError):
                p.channel_tests_enabled({}, snapshot)
        for environment in ({"CHANNEL_TEST_ENABLED": "unknown"}, {"CHANNEL_TEST_FREQUENCY": "1.5"}):
            with self.assertRaises(p.h.DeploymentError):
                p.channel_tests_enabled(environment, {"options": [], "channel_test_active": 0})

    def test_internal_submitters_need_disabled_schedule_and_zero_active_tests(self):
        native = {"Config": {"Env": []}, "HostConfig": {"PortBindings": {}, "NetworkMode": "app-net"}}
        with patch.object(p, "monitor_implementation_verified", return_value="modern"), patch.object(p, "monitor_snapshot", return_value={"options": [], "channel_test_active": 0}):
            self.assertTrue(p.assert_internal_submitters(native))
        for snapshot in ({"options": [], "channel_test_active": 1}, {"options": [{"key": "monitor_setting.auto_test_channel_enabled", "value": "true"}], "channel_test_active": 0}):
            with patch.object(p, "monitor_implementation_verified", return_value="modern"), patch.object(p, "monitor_snapshot", return_value=snapshot), self.assertRaises(p.h.DeploymentError):
                p.assert_internal_submitters(native)

    def test_host_network_or_public_bind_cannot_bypass_gate(self):
        for host in ({"NetworkMode": "host", "PortBindings": {}}, {"NetworkMode": "app-net", "PortBindings": {"3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3000"}]}}):
            with self.subTest(host=host), self.assertRaises(p.h.DeploymentError):
                p.assert_private_ingress({"HostConfig": host})

    def test_owned_admission_checks_current_conf_drain_instances_and_actual_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = pathlib.Path(directory) / "nginx.conf"; conf.write_bytes(b"owned gate")
            drain = pathlib.Path(directory) / "DRAIN"; drain.write_bytes(p.MARKER)
            state = {"gated_nginx_sha256": hashlib.sha256(b"owned gate").hexdigest(), "gateway_id": "g" * 64,
                     "native_id": "n" * 64, "native_image": "sha256:" + "1" * 64, "nginx_id": "x" * 64}
            def inspect(name):
                if name == p.h.IMAGE_GATEWAY: return {"Id": "g" * 64, "State": {"Running": True}}
                if name == p.NGINX: return {"Id": "x" * 64, "State": {"Running": True}}
                return {"Id": "n" * 64, "Image": state["native_image"], "State": {"Running": True}}
            with patch.object(p, "CONF", conf), patch.object(p, "DRAIN", drain), patch.object(p, "inspect", side_effect=inspect), patch.object(p, "local_get", return_value={"draining": True, "accepting": False}), patch.object(p, "assert_internal_submitters", return_value=True), patch.object(p, "probe_maintenance", return_value=True):
                self.assertTrue(p.assert_owned_admission(state))
                conf.write_bytes(b"external edit")
                with self.assertRaises(p.h.DeploymentError): p.assert_owned_admission(state)

    def test_gate_probe_covers_aliases_and_checks_operation_and_no_submit_headers(self):
        good = {"error": {"code": "relay_maintenance", "message": "No generation submitted " + p.OP}}
        headers = {"X-XingTu-Image-Submission-State": "not_submitted", "X-XingTu-Relay-Request-ID": "owned"}
        with patch.object(p, "nginx_probe", return_value=(503, headers, good)) as probe:
            self.assertTrue(p.probe_maintenance())
            paths = {call.args[1] for call in probe.call_args_list}
            self.assertTrue({"/v1/edits", "/pg/chat/completions", "/api/channel/test", "/api/channel/test/1"} <= paths)
        for reply in ((200, headers, good), (503, {}, good), (503, headers, {"error": {"code": "relay_maintenance", "message": "another operation"}})):
            with patch.object(p, "nginx_probe", return_value=reply), self.assertRaises(p.h.DeploymentError):
                p.probe_maintenance()

    def test_unknown_create_is_reconciled_only_to_owned_exact_candidate(self):
        name = p.h.NODY
        target = {"candidate_image": "sha256:" + "c" * 64, "app_sha256": "a" * 64}
        state = {"targets": {name: {"before_id": "b" * 64, "new_id": None}}}
        candidate = {"Id": "d" * 64, "Name": "/" + name, "Image": target["candidate_image"],
                     "Config": {"Labels": {p.h.OP_LABEL: p.OP, "com.aixingtuyun.image-fixes-app-sha256": target["app_sha256"]}}}
        with patch.object(p, "inspect_optional", return_value=candidate), patch.object(p, "save") as save:
            self.assertEqual(p.reconcile_create(name, state, target), "d" * 64)
            self.assertEqual(state["targets"][name]["new_id"], "d" * 64)
            save.assert_called()
        candidate["Config"]["Labels"][p.h.OP_LABEL] = "foreign"
        with patch.object(p, "inspect_optional", return_value=candidate), self.assertRaises(p.h.DeploymentError):
            p.reconcile_create(name, state, target)

    def test_unknown_create_absence_is_distinct_from_unknown_docker_failure(self):
        name = p.h.NODY
        state = {"targets": {name: {"before_id": "b" * 64, "new_id": None}}}
        with patch.object(p, "inspect_optional", return_value=None), patch.object(p, "docker") as docker:
            self.assertIsNone(p.reconcile_create(name, state, {"candidate_image": "sha256:" + "c" * 64, "app_sha256": "a" * 64}))
            docker.assert_not_called()
        with patch.object(p, "inspect", side_effect=p.DockerAPIError(500)), self.assertRaises(p.DockerAPIError):
            p.inspect_optional(name)
        with patch.object(p, "inspect", side_effect=p.DockerAPIError(404)):
            self.assertIsNone(p.inspect_optional(name))

    def test_missing_admission_proof_cannot_produce_three_idle_samples(self):
        sample = {"image_jobs_active": 0, "banana_active_requests": 0, "nody_handler_threads": 1,
                  "image25_handler_threads": 1, "native_reading": 0, "native_writing": 1}
        with patch.object(p, "idle_sample", return_value=sample), patch.object(p.time, "sleep"), patch.object(p.time, "monotonic", side_effect=[0, 1, 46]), self.assertRaises(p.h.DeploymentError):
            p.assert_quiet({})

    def test_rollback_candidate_ownership_and_activity_are_verified_before_stop(self):
        name = p.h.NODY; target = {"candidate_image": "sha256:" + "c" * 64, "app_sha256": "a" * 64}
        info = {"Id": "d" * 64, "Name": "/" + name, "Image": target["candidate_image"], "State": {"Running": True},
                "Config": {"Labels": {p.h.OP_LABEL: p.OP, "com.aixingtuyun.image-fixes-app-sha256": target["app_sha256"]}}}
        with patch.object(p, "inspect", return_value=info), patch.object(p, "assert_owned_admission", return_value=True), patch.object(p, "adapter_active", return_value=1), patch.object(p.time, "sleep"), patch.object(p.time, "monotonic", side_effect=[0, 1, 46]), self.assertRaises(p.h.DeploymentError):
            p.assert_rollback_idle(name, info["Id"], target, {})
        info["Config"]["Labels"][p.h.OP_LABEL] = "foreign"
        with patch.object(p, "inspect", return_value=info), self.assertRaises(p.h.DeploymentError):
            p.assert_candidate_owned(info, name, target)

    def test_busy_rollback_never_calls_stop_rename_or_disconnect(self):
        name = p.h.NODY; target = {"new_id": "d" * 64}
        manifest = {"targets": {name: {"candidate_image": "sha256:" + "c" * 64, "app_sha256": "a" * 64}}}
        with patch.object(p, "assert_rollback_idle", side_effect=p.h.DeploymentError("activity unknown")), patch.object(p, "docker") as docker, patch.object(p, "save") as save:
            with self.assertRaises(p.h.DeploymentError):
                p.retain_failed_candidate(name, target, manifest, {})
            docker.assert_not_called(); save.assert_not_called()

    def test_rollback_needs_three_fresh_ownership_checked_quiet_samples(self):
        name = p.h.NODY; target = {"candidate_image": "sha256:" + "c" * 64, "app_sha256": "a" * 64}
        info = {"Id": "d" * 64, "Name": "/" + name, "Image": target["candidate_image"], "State": {"Running": True},
                "Config": {"Labels": {p.h.OP_LABEL: p.OP, "com.aixingtuyun.image-fixes-app-sha256": target["app_sha256"]}}}
        with patch.object(p, "inspect", return_value=info) as inspect, patch.object(p, "assert_owned_admission", return_value=True) as gate, patch.object(p, "adapter_active", side_effect=[0, 1, 0, 0, 0]) as active, patch.object(p.time, "sleep"):
            p.assert_rollback_idle(name, info["Id"], target, {})
        self.assertEqual(active.call_count, 5); self.assertEqual(gate.call_count, 5); self.assertEqual(inspect.call_count, 5)

    def test_confirmed_stopped_candidate_requires_ownership_but_no_forced_handler_probe(self):
        name = p.h.NODY; target = {"candidate_image": "sha256:" + "c" * 64, "app_sha256": "a" * 64}
        info = {"Id": "d" * 64, "Name": "/" + name, "Image": target["candidate_image"], "State": {"Running": False},
                "Config": {"Labels": {p.h.OP_LABEL: p.OP, "com.aixingtuyun.image-fixes-app-sha256": target["app_sha256"]}}}
        with patch.object(p, "inspect", return_value=info), patch.object(p, "assert_owned_admission", return_value=True), patch.object(p, "adapter_active") as active:
            p.assert_rollback_idle(name, info["Id"], target, {})
            active.assert_not_called()

    def test_restore_verification_checks_config_mounts_and_native_network_connectivity(self):
        old = {"Id": "b" * 64, "Image": "sha256:" + "a" * 64, "Name": "/" + p.h.NODY,
               "State": {"Running": True}, "Config": {"Env": ["TOKEN=private"]}, "HostConfig": {}, "Mounts": [],
               "NetworkSettings": {"Networks": {"app-net": {"Aliases": [p.h.NODY], "IPAddress": "172.18.0.2"}}}}
        current = copy.deepcopy(old)
        current["NetworkSettings"]["Networks"]["app-net"]["IPAddress"] = "172.18.0.3"
        with patch.object(p, "inspect", return_value=current), patch.object(p, "local_get", return_value={"ok": True}), patch.object(p, "native_adapter_health") as health:
            p.verify_restored_adapter(p.h.NODY, old)
            health.assert_called_once_with(p.h.NODY)
        current["Config"]["Env"] = ["TOKEN=changed"]
        with patch.object(p, "inspect", return_value=current), self.assertRaises(p.h.DeploymentError):
            p.verify_restored_adapter(p.h.NODY, old)

if __name__ == "__main__":
    unittest.main()
