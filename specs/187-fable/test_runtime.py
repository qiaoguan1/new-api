"""Offline contracts for the binary-only native deployment state machine."""
import copy
import json
import subprocess
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("deploy_runtime", Path(__file__).with_name("deploy_runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_single_file_nginx_bind_is_rejected_before_deployment(self):
        info = {"Mounts": [{"Source": str(runtime.NGINX), "Destination": "/etc/nginx/conf.d/default.conf", "RW": False}]}
        with self.assertRaisesRegex(RuntimeError, "single-file"):
            runtime.verify_nginx_mount(info)

    def test_only_verified_readonly_nginx_directory_bind_is_accepted(self):
        info = {"Mounts": [{"Source": str(runtime.STACK / "nginx/conf.d"), "Destination": "/etc/nginx/conf.d", "RW": False}]}
        runtime.verify_nginx_mount(info)
        info["Mounts"][0]["RW"] = True
        with self.assertRaises(RuntimeError):
            runtime.verify_nginx_mount(info)

    def test_unseen_container_configuration_is_restored_without_success(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = runtime.RuntimeDeployment(root=Path(directory))
            deployment._nginx_expected = "before"
            deployment.atomic_text = Mock()
            deployment.run = Mock(side_effect=["unseen gate", "before", "", ""])
            with self.assertRaisesRegex(RuntimeError, "did not receive"):
                deployment.nginx_reload("gate")
            self.assertEqual(deployment._nginx_expected, "before")
            self.assertEqual(deployment.atomic_text.call_args_list[-1].args[1:], ("before", "gate"))

    def test_status_transport_preflight_uses_only_verified_existing_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = runtime.RuntimeDeployment(root=Path(directory))
            for tool in ["wget", "curl"]:
                with self.subTest(tool=tool):
                    deployment.run = Mock(return_value=tool + "\n")
                    deployment.select_status_transport()
                    self.assertEqual(deployment.status_command()[3], tool)
                    self.assertEqual(deployment.status_command()[-1], "http://127.0.0.1:18087/")
            deployment.run = Mock(return_value="unverified-tool")
            with self.assertRaises(RuntimeError):
                deployment.select_status_transport()

    def test_dry_preflight_command_evidence_is_unique_across_invocations(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", "")):
            first = runtime.RuntimeDeployment(root=Path(directory))
            second = runtime.RuntimeDeployment(root=Path(directory))
            first.run(["read-only-command"])
            second.run(["read-only-command"])
            evidence = list(Path(directory).glob("runtime-command-*.json"))
            self.assertEqual(len(evidence), 2)
            self.assertNotEqual(first._invocation, second._invocation)

    def test_proof_gate_requires_success_and_exact_image_for_all_proofs(self):
        image = "sha256:" + "a" * 64
        candidate = {"image": image}
        pipeline = {"success": True, "phase": "verified", "image": image, "production_unchanged": True}
        fake = {"model": runtime.MODEL, "image": image, "billing_exact": True, "compatibility_verified": True,
                "results": [{"http": 200, "billing_exact": True}]}
        real = {"model": runtime.MODEL, "image": image, "billing_exact": True,
                "results": [{"http": 200, "billing_exact": True, "response_model": runtime.MODEL}]}
        runtime.verify_proofs(candidate, pipeline, fake, real)
        for name in ["pipeline", "fake", "real"]:
            objects = {"pipeline": copy.deepcopy(pipeline), "fake": copy.deepcopy(fake), "real": copy.deepcopy(real)}
            objects[name]["image"] = "sha256:" + "b" * 64
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                runtime.verify_proofs(candidate, objects["pipeline"], objects["fake"], objects["real"])
        for value in [False, 1, "true"]:
            with self.assertRaises(RuntimeError):
                runtime.verify_proofs(candidate, {**pipeline, "success": value}, fake, real)

    def test_compose_change_is_only_new_api_image(self):
        text = "services:\n  new-api:\n    image: old\n    environment:\n      KEEP: value\n    networks: [app-net, stack-internal]\n  redis:\n    image: redis:7\nnetworks:\n  app-net:\n    external: true\n"
        changed = runtime.change_native_image(text, "sha256:" + "a" * 64)
        import yaml
        expected = yaml.safe_load(text)
        expected["services"]["new-api"]["image"] = "sha256:" + "a" * 64
        self.assertEqual(yaml.safe_load(changed), expected)
        self.assertEqual(yaml.safe_load(changed)["services"]["redis"], yaml.safe_load(text)["services"]["redis"])

    def test_maintenance_gate_is_text_image_only_and_rejects_existing_shadow_locations(self):
        text = "server {\n    server_name api.aixingtuyun.com aixingtuyun.com www.aixingtuyun.com;\n    location / { proxy_pass http://native; }\n    location /v1/videos { proxy_pass http://video; }\n    location /callbacks { proxy_pass http://video; }\n}\n"
        gated = runtime.maintenance_config(text)
        self.assertIn("relay_maintenance", gated)
        self.assertIn("images/(generations|edits)", gated)
        self.assertIn("location /v1/videos { proxy_pass http://video; }", gated)
        self.assertIn("location /callbacks { proxy_pass http://video; }", gated)
        catalog = text.replace("    location / {", "    location = /v1/models { proxy_pass http://catalog; }\n    location ~ ^/v1/messages { proxy_pass http://native; }\n    location / {")
        first_gate = runtime.maintenance_config(catalog)
        self.assertIn("location = /v1/models { proxy_pass http://catalog; }", first_gate)
        self.assertLess(first_gate.index("issue187-native-maintenance"), first_gate.index("location ~ ^/v1/messages"))
        with self.assertRaises(RuntimeError):
            runtime.maintenance_config(text.replace("    location / {", "    location = /v1/messages { return 200; }\n    location / {"))

    def fixture(self, directory):
        deployment = runtime.RuntimeDeployment(root=Path(directory))
        before = {"native_image": runtime.BASELINE_IMAGE, "native_env": {"KEY": "private-test"},
                  "native_networks": list(runtime.REQUIRED_NETWORKS), "compose": "before", "nginx": "before"}
        deployment.preflight = Mock(return_value=({"image": "sha256:" + "a" * 64}, before, "after", "gate"))
        deployment.backup = Mock()
        deployment.create_drain = Mock()
        deployment.nginx_reload = Mock()
        deployment.remove_drain = Mock()
        deployment.wait_drained = Mock()
        deployment.write_compose = Mock()
        deployment.compose_up = Mock()
        deployment.ready = Mock()
        return deployment

    def test_drain_timeout_reopens_original_gate_without_recreating_native(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = self.fixture(directory)
            deployment.wait_drained.return_value = False
            with self.assertRaises(RuntimeError):
                deployment.deploy()
            deployment.compose_up.assert_not_called()
            deployment.write_compose.assert_not_called()
            self.assertEqual(deployment.nginx_reload.call_args_list[-1].args[0], "before")
            deployment.remove_drain.assert_called_once()

    def test_failed_switch_restores_compose_and_native_but_never_database(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = self.fixture(directory)
            deployment.wait_drained.return_value = True
            deployment.ready.side_effect = [RuntimeError("new runtime failed"), None]
            with self.assertRaises(RuntimeError):
                deployment.deploy()
            self.assertEqual(deployment.write_compose.call_args_list[-1].args[0], "before")
            self.assertEqual(deployment.compose_up.call_count, 2)
            self.assertEqual(deployment.ready.call_args_list[-1].args[0], runtime.BASELINE_IMAGE)
            deployment.remove_drain.assert_called_once()

    def test_ready_runtime_is_not_restarted_if_gate_release_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = self.fixture(directory)
            deployment.wait_drained.return_value = True
            deployment.nginx_reload.side_effect = [None, RuntimeError("release failed")]
            with self.assertRaises(RuntimeError):
                deployment.deploy()
            self.assertEqual(deployment.compose_up.call_count, 1)
            self.assertEqual(deployment.write_compose.call_count, 1)
            self.assertTrue((Path(directory) / "runtime-recovery-required.json").exists())

    def test_gate_install_failure_is_restored_before_drain_release(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = self.fixture(directory)
            deployment.nginx_reload.side_effect = [RuntimeError("install failed midway"), None]
            with self.assertRaises(RuntimeError):
                deployment.deploy()
            self.assertEqual(deployment.nginx_reload.call_args_list[-1].args[0], "before")
            deployment.compose_up.assert_not_called()
            deployment.remove_drain.assert_called_once()

    def test_partial_owned_drain_write_can_be_cleaned_without_touching_other_owner(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "DRAIN", Path(directory) / "DRAIN"):
            deployment = runtime.RuntimeDeployment(root=Path(directory))
            with patch.object(runtime.os, "fsync", side_effect=OSError("sync failed")):
                with self.assertRaises(OSError):
                    deployment.create_drain()
            self.assertTrue(runtime.DRAIN.exists())
            deployment.remove_drain()
            self.assertFalse(runtime.DRAIN.exists())

    def test_failed_baseline_recovery_keeps_gate_and_drain_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = self.fixture(directory)
            deployment.wait_drained.return_value = True
            deployment.ready.side_effect = [RuntimeError("candidate failed"), RuntimeError("baseline failed")]
            with self.assertRaises(RuntimeError):
                deployment.deploy()
            deployment.remove_drain.assert_not_called()
            self.assertEqual(deployment.nginx_reload.call_count, 1)

    def probe_drain(self, output, *, active=0, unknown=0, expected=False):
        """Exercise the real drain loop with a clock, DB and status transport."""
        class Clock:
            value = 0.0

            def now(self):
                return self.value

            def sleep(self, duration):
                # Three samples cover the unchanged 60-second guard without a
                # wall-clock wait; never skip the loop's monotonic checks.
                self.value += 20

        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            deployment = self.fixture(directory)
            deployment.wait_drained = runtime.RuntimeDeployment.wait_drained.__get__(deployment)
            connection = Mock()
            connection.execute.side_effect = lambda sql: Mock(fetchone=Mock(return_value=(unknown if "IS NULL" in sql else active,)))
            context = Mock()
            context.__enter__ = Mock(return_value=connection)
            context.__exit__ = Mock(return_value=False)
            with patch.object(runtime.sqlite3, "connect", return_value=context), \
                    patch.object(runtime.subprocess, "run", side_effect=output if isinstance(output, Exception) else None,
                                 return_value=output if not isinstance(output, Exception) else None) as transport, \
                    patch.object(runtime.time, "monotonic", side_effect=clock.now), \
                    patch.object(runtime.time, "sleep", side_effect=clock.sleep):
                if expected:
                    self.assertTrue(deployment.wait_drained())
                else:
                    with self.assertRaises(RuntimeError):
                        deployment.deploy()
                    deployment.compose_up.assert_not_called()
                    deployment.write_compose.assert_not_called()
                    deployment.remove_drain.assert_called_once()
            evidence = list(Path(directory).glob("runtime-drain-*.json"))
            self.assertEqual(len(evidence), 1)
            self.assertIn(deployment._invocation, evidence[0].name)
            proof = json.loads(evidence[0].read_text())
            self.assertEqual(proof["deadline_seconds"], 60)
            self.assertEqual(proof["drained"], expected)
            self.assertLessEqual(len(proof["samples"]), 64)
            self.assertFalse(proof["samples_truncated"])
            for sample in proof["samples"]:
                self.assertTrue(all(type(value) is int for value in sample.values()))
            self.assertNotIn("sensitive", evidence[0].read_text())
            self.assertTrue(all(call.kwargs["timeout"] <= 3 for call in transport.call_args_list))
            return proof

    def test_real_drain_transport_rc_127_is_diagnosed_and_aborts_before_swap(self):
        proof = self.probe_drain(subprocess.CompletedProcess([], 127, "", "sensitive missing-tool diagnostic"))
        self.assertEqual(proof["classification"], "status_transport_error")
        self.assertTrue(all(row["transport_rc"] == 127 for row in proof["samples"]))

    def test_real_drain_transport_timeout_is_diagnosed_and_aborts_before_swap(self):
        proof = self.probe_drain(subprocess.TimeoutExpired(["sensitive"], 3, stderr="sensitive timeout diagnostic"))
        self.assertEqual(proof["classification"], "status_transport_timeout")
        self.assertTrue(all(row["timed_out"] == 1 for row in proof["samples"]))

    def test_real_drain_invalid_status_is_diagnosed_and_aborts_before_swap(self):
        proof = self.probe_drain(subprocess.CompletedProcess([], 0, "sensitive invalid response", ""))
        self.assertEqual(proof["classification"], "status_parse_error")
        self.assertTrue(all(row["parse_ok"] == 0 for row in proof["samples"]))

    def test_real_drain_active_images_are_diagnosed_and_abort_before_swap(self):
        proof = self.probe_drain(subprocess.CompletedProcess([], 0, "Reading: 0 Writing: 1 Waiting: 0", ""), active=1)
        self.assertEqual(proof["classification"], "active_image_jobs")
        self.assertTrue(all(row["active_images"] == 1 for row in proof["samples"]))

    def test_real_drain_busy_writers_are_diagnosed_and_abort_before_swap(self):
        proof = self.probe_drain(subprocess.CompletedProcess([], 0, "Reading: 0 Writing: 2 Waiting: 0", ""))
        self.assertEqual(proof["classification"], "nginx_connections_busy")
        self.assertTrue(all(row["writing"] == 2 for row in proof["samples"]))

    def test_real_drain_three_clean_polls_pass_without_relaxing_guard(self):
        proof = self.probe_drain(subprocess.CompletedProcess([], 0, "Reading: 0 Writing: 1 Waiting: 99", ""), expected=True)
        self.assertEqual(proof["classification"], "drained")
        self.assertEqual([row["quiet_count"] for row in proof["samples"]], [1, 2, 3])
        self.assertTrue(all(row["reading"] == 0 and row["writing"] <= 1 and row["active_images"] == 0
                            for row in proof["samples"]))

    def test_real_drain_unknown_image_state_is_private_and_fails_closed(self):
        proof = self.probe_drain(subprocess.CompletedProcess([], 0, "Reading: 0 Writing: 1 Waiting: 0", ""), unknown=1)
        self.assertEqual(proof["classification"], "unknown_image_state")
        self.assertEqual(proof["samples"][0]["unknown_images"], 1)


if __name__ == "__main__":
    unittest.main()
