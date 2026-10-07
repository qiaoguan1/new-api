"""Local, non-production tests for the explicitly approved shared proxy remount."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("proxy_mount", Path(__file__).with_name("proxy_mount.py"))
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class ProxyMountTests(unittest.TestCase):
    def test_live_proxy_empty_dns_representation_is_not_a_setting_change(self):
        before = self.nginx()
        before["HostConfig"].update(Dns=[], DnsSearch=[], ExtraHosts=[])
        after = copy.deepcopy(before)
        after["HostConfig"].update(Dns=None, DnsSearch=None, ExtraHosts=None)
        proxy.check_recreated_proxy(before, after, directory=False)
        after["HostConfig"]["Dns"] = ["8.8.8.8"]
        with self.assertRaises(RuntimeError):
            proxy.check_recreated_proxy(before, after, directory=False)

    def test_absent_host_override_representation_only_is_normalized(self):
        self.assertEqual(proxy.optional_host_list(None), proxy.optional_host_list([]))
        self.assertNotEqual(proxy.optional_host_list([]), proxy.optional_host_list(["8.8.8.8"]))
        self.assertNotEqual(proxy.optional_host_list(["first", "second"]), proxy.optional_host_list(["second", "first"]))
        with self.assertRaises(RuntimeError):
            proxy.optional_host_list("8.8.8.8")

    def test_service_identity_survives_private_json_roundtrip(self):
        info = {"Id": "same", "Image": "same", "Config": {"Env": ["A=B"]}, "State": {"StartedAt": "same"},
                "NetworkSettings": {"Networks": {"app-net": {}}},
                "Mounts": [{"Type": "bind", "Source": "/source", "Destination": "/target", "RW": False, "Propagation": "rprivate"}]}
        identity = proxy.service_identity(info)
        self.assertEqual(identity, json.loads(json.dumps(identity)))
        changed = copy.deepcopy(info)
        changed["Mounts"][0]["RW"] = True
        self.assertNotEqual(identity, proxy.service_identity(changed))

    def compose(self, *, long=False, owner="base"):
        volume = ({"type": "bind", "source": str(proxy.DEFAULT), "target": proxy.FILE_TARGET,
                   "read_only": True, "bind": {"create_host_path": False}} if long else
                  str(proxy.DEFAULT) + ":" + proxy.FILE_TARGET + ":ro")
        base = {"services": {"nginx": {"image": "nginx:alpine", "volumes": ["/cert:/cert:ro"]},
                             "new-api": {"image": "untouched", "environment": {"PRIVATE": "unchanged"}}}}
        override = {"services": {"nginx": {"environment": {"KEEP": "yes"}}, "redis": {"image": "redis:7"}}}
        (base if owner == "base" else override)["services"]["nginx"].setdefault("volumes", []).append(volume)
        import yaml
        return yaml.safe_dump(base), yaml.safe_dump(override)

    def test_only_two_nginx_mount_values_change_short_and_long_in_either_file(self):
        import yaml
        for long in [False, True]:
            for owner in ["base", "override"]:
                with self.subTest(long=long, owner=owner):
                    base, override = self.compose(long=long, owner=owner)
                    changed = proxy.change_proxy_mounts(base, override, proxy.STATIC_MAIN)
                    before = [yaml.safe_load(base), yaml.safe_load(override)]
                    after = [yaml.safe_load(changed[0]), yaml.safe_load(changed[1])]
                    selected = 0 if owner == "base" else 1
                    self.assertEqual(after[1-selected], before[1-selected])
                    volumes = after[selected]["services"]["nginx"].pop("volumes")
                    before[selected]["services"]["nginx"].pop("volumes")
                    self.assertEqual(after, before)
                    rows = [proxy.volume_row(v) for v in volumes]
                    self.assertIn((str(proxy.CONFIG_DIR), proxy.DIRECTORY_TARGET, True), rows)
                    self.assertIn((str(proxy.STATIC_MAIN), proxy.MAIN_TARGET, True), rows)
                    self.assertNotIn((str(proxy.DEFAULT), proxy.FILE_TARGET, True), rows)
                    if long:
                        self.assertEqual(volumes[-2]["bind"], {"create_host_path": False})

    def test_relative_source_is_resolved_without_changing_other_relative_bind_bytes(self):
        import yaml
        base, override = self.compose()
        relative = base.replace(str(proxy.DEFAULT), "./nginx/conf.d/default.conf").replace("/cert:/cert:ro", "./nginx/certs:/cert:ro")
        changed, _ = proxy.change_proxy_mounts(relative, override, proxy.STATIC_MAIN)
        volumes = yaml.safe_load(changed)["services"]["nginx"]["volumes"]
        self.assertIn("./nginx/certs:/cert:ro", volumes)
        self.assertEqual(proxy.bind_source("./nginx/conf.d/default.conf"), str(proxy.DEFAULT))
        with self.assertRaises(RuntimeError):
            proxy.change_proxy_mounts(relative.replace("./nginx/conf.d/default.conf", "../nginx/conf.d/default.conf"), override, proxy.STATIC_MAIN)

    def test_rejects_missing_duplicate_writable_and_preexisting_main_mount(self):
        base, override = self.compose()
        invalid = [base.replace(":ro", ":rw"), base.replace(str(proxy.DEFAULT), "/wrong/default.conf"),
                   base + "\nservices: {}\n"]
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                proxy.change_proxy_mounts(bad, override, proxy.STATIC_MAIN)
        import yaml
        parsed = yaml.safe_load(base)
        volumes = parsed["services"]["nginx"]["volumes"]
        for extra in [str(proxy.DEFAULT) + ":" + proxy.FILE_TARGET + ":ro", "/old/main:" + proxy.MAIN_TARGET + ":ro",
                      str(proxy.CONFIG_DIR) + ":" + proxy.DIRECTORY_TARGET + ":ro"]:
            with self.subTest(extra=extra), self.assertRaises(RuntimeError):
                proxy.change_proxy_mounts(yaml.safe_dump({**parsed, "services": {**parsed["services"], "nginx": {
                    **parsed["services"]["nginx"], "volumes": volumes + [extra]}}}), override, proxy.STATIC_MAIN)

    def test_static_include_preserves_all_bytes_other_than_one_directive(self):
        original = "user nginx;\nevents {}\nhttp {\n    include /etc/nginx/conf.d/*.conf;\n    # retain\n}\n"
        expected = original.replace("/etc/nginx/conf.d/*.conf", "/etc/nginx/conf.d/default.conf")
        self.assertEqual(proxy.static_main(original), expected)
        for invalid in [original.replace("*.conf", "default.conf"), original + "include /etc/nginx/conf.d/*.conf;\n",
                        "# include /etc/nginx/conf.d/*.conf;\nhttp {}"]:
            with self.assertRaises(RuntimeError):
                proxy.static_main(invalid)

    def test_loaded_config_rejects_dormant_config_and_unrelated_output_changes(self):
        text = "# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/conf.d/*.conf; }\n" + \
               "# configuration file /etc/nginx/conf.d/default.conf:\nserver {}\n"
        proxy.check_loaded_configuration(text, proxy.static_main(text))
        for invalid in [proxy.static_main(text) + "# configuration file /etc/nginx/conf.d/dormant.conf:\nserver {}\n",
                        proxy.static_main(text).replace("server {}", "server { changed; }")]:
            with self.assertRaises(RuntimeError):
                proxy.check_loaded_configuration(text, invalid)

    def test_resolved_services_require_only_exact_mount_changes(self):
        before = {"services": {"nginx": {"image": "same", "volumes": [
            {"type": "bind", "source": str(proxy.DEFAULT), "target": proxy.FILE_TARGET, "read_only": True},
            {"type": "bind", "source": "/cert", "target": "/cert", "read_only": True}]},
            "new-api": {"image": "unchanged"}}, "networks": {"a": {}}}
        after = copy.deepcopy(before)
        after["services"]["nginx"]["volumes"][0].update(source=str(proxy.CONFIG_DIR), target=proxy.DIRECTORY_TARGET)
        after["services"]["nginx"]["volumes"].append({"type": "bind", "source": str(proxy.STATIC_MAIN),
            "target": proxy.MAIN_TARGET, "read_only": True, "bind": {"create_host_path": True}})
        proxy.check_resolved_compose(before, after)
        for mutate in [lambda x: x["services"]["new-api"].update(image="changed"),
                       lambda x: x["services"]["nginx"].update(environment={"NEW": "not allowed"}),
                       lambda x: x["services"]["nginx"]["volumes"][1].update(read_only=False)]:
            bad = copy.deepcopy(after)
            mutate(bad)
            with self.assertRaises(RuntimeError):
                proxy.check_resolved_compose(before, bad)

    def nginx(self):
        return {"Id": "original", "Image": "sha256:" + "a"*64, "State": {"Running": True},
                "Config": {"Env": ["PRIVATE=test"], "StopSignal": "SIGQUIT"},
                "NetworkSettings": {"Networks": {"app-net": {}, "internal": {}}},
                "HostConfig": {"PortBindings": {"80/tcp": [{"HostPort": "80"}]},
                               "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0}},
                "Mounts": [{"Type": "bind", "Source": str(proxy.DEFAULT), "Destination": proxy.FILE_TARGET,
                            "RW": False, "Propagation": "rprivate"},
                           {"Type": "bind", "Source": "/cert", "Destination": "/cert", "RW": False,
                            "Propagation": "rprivate"}]}

    def test_recreated_proxy_requires_identical_image_env_network_ports_and_other_mounts(self):
        before = self.nginx()
        after = copy.deepcopy(before)
        after["Id"] = "recreated"
        after["Mounts"][0].update(Source=str(proxy.CONFIG_DIR), Destination=proxy.DIRECTORY_TARGET)
        after["Mounts"].append({"Type": "bind", "Source": str(proxy.STATIC_MAIN), "Destination": proxy.MAIN_TARGET,
                                 "RW": False, "Propagation": "rprivate"})
        proxy.check_recreated_proxy(before, after, directory=True)
        for mutate in [lambda x: x.update(Image="wrong"), lambda x: x["Config"].update(Env=["changed"]),
                       lambda x: x["NetworkSettings"]["Networks"].pop("internal"),
                       lambda x: x["Mounts"][1].update(RW=True),
                       lambda x: x["HostConfig"].update(PortBindings={})]:
            bad = copy.deepcopy(after)
            mutate(bad)
            with self.assertRaises(RuntimeError):
                proxy.check_recreated_proxy(before, bad, directory=True)
        proxy.check_recreated_proxy(before, before, directory=False)

    def test_recreated_proxy_allows_environment_permutation_but_not_changed_values(self):
        before = self.nginx()
        before["Config"]["Env"] = ["PATH=/bin", "KEEP=value=with=equals", "EMPTY="]
        after = copy.deepcopy(before)
        after["Config"]["Env"].reverse()
        proxy.check_recreated_proxy(before, after, directory=False)
        after["Config"]["Env"][1] = "KEEP=changed"
        with self.assertRaises(RuntimeError):
            proxy.check_recreated_proxy(before, after, directory=False)

    def test_environment_mapping_rejects_malformed_duplicate_or_missing_fields(self):
        self.assertEqual(proxy.environment_map(["KEEP=a=b", "EMPTY="]), {"KEEP": "a=b", "EMPTY": ""})
        for invalid in [None, "PATH=/bin", ["NO_EQUALS"], ["=missing_name"], [None], [7],
                        ["KEEP=x", "KEEP=x"], ["KEEP=x", "KEEP=y"], ["KEEP=x\x00y"]]:
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                proxy.environment_map(invalid)
        before = self.nginx()
        for after_env in [[], ["PRIVATE=test", "EXTRA=value"], ["PRIVATE=test", "PRIVATE=test"]]:
            after = copy.deepcopy(before)
            after["Config"]["Env"] = after_env
            with self.subTest(after_env=after_env), self.assertRaises(RuntimeError):
                proxy.check_recreated_proxy(before, after, directory=False)

    def test_environment_order_tolerance_does_not_relax_other_process_fields(self):
        before = self.nginx()
        before["Config"].update(Env=["A=one", "B=two"], Cmd=["nginx", "-g", "daemon off;"])
        after = copy.deepcopy(before)
        after["Config"]["Env"].reverse()
        after["Config"]["Cmd"].reverse()
        with self.assertRaises(RuntimeError):
            proxy.check_recreated_proxy(before, after, directory=False)

    def fixture(self, directory):
        maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
        maintenance._runtime = Mock(h=Mock())
        before = {"base_compose": "base", "override_compose": "override", "nginx_inspect": self.nginx(),
                  "default": "default", "main": "main", "loaded": "loaded", "native_identity": {},
                  "gateway_identity": {}, "resolved_compose": {}}
        maintenance.preflight = Mock(return_value=(before, ("base2", "override"), "main2"))
        maintenance.backup = Mock()
        maintenance.test_canary = Mock()
        maintenance.create_drain = Mock()
        maintenance.remove_drain = Mock()
        maintenance.wait_images_quiet = Mock(return_value=True)
        maintenance.write_compose = Mock()
        maintenance.assert_live_preconditions = Mock()
        maintenance.resolve_compose = Mock(return_value={})
        maintenance.stop_proxy = Mock()
        maintenance.disable_restart = Mock()
        maintenance.restore_restart = Mock()
        maintenance.h.inspect = Mock(return_value={"State": {"Running": False}})
        maintenance.compose_up = Mock()
        maintenance.ready = Mock()
        return maintenance

    def test_busy_images_do_not_write_compose_or_stop_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = self.fixture(directory)
            maintenance.wait_images_quiet.return_value = False
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            maintenance.write_compose.assert_not_called()
            maintenance.stop_proxy.assert_not_called()
            maintenance.compose_up.assert_not_called()
            maintenance.remove_drain.assert_called_once()

    def test_invalid_canary_does_not_start_maintenance_or_write_compose(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = self.fixture(directory)
            maintenance.test_canary.side_effect = RuntimeError("test rejected")
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            maintenance.create_drain.assert_not_called()
            maintenance.write_compose.assert_not_called()
            maintenance.stop_proxy.assert_not_called()

    def test_graceful_stop_timeout_retains_pending_evidence_without_recreation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.stop_proxy.return_value = False
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            maintenance.compose_up.assert_not_called()
            maintenance.remove_drain.assert_not_called()
            self.assertEqual(maintenance.write_compose.call_count, 1)
            evidence = json.loads((Path(directory)/"proxy-mount-recovery-required.json").read_text())
            self.assertTrue(evidence["graceful_shutdown_pending"])
            self.assertTrue(evidence["no_automatic_replay"])

    def test_ambiguous_signal_delivery_retains_pending_evidence_and_drain(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.stop_proxy.side_effect = RuntimeError("CLI result unknown")
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            maintenance.compose_up.assert_not_called()
            maintenance.remove_drain.assert_not_called()
            self.assertEqual(maintenance.write_compose.call_count, 1)

    def test_replacement_proxy_must_drain_before_rollback_recreation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.ready.side_effect = [RuntimeError("not verified"), None]
            replacement = self.nginx()
            replacement["Id"] = "replacement"
            replacement["Mounts"][0].update(Source=str(proxy.CONFIG_DIR), Destination=proxy.DIRECTORY_TARGET)
            replacement["Mounts"].append({"Type": "bind", "Source": str(proxy.STATIC_MAIN), "Destination": proxy.MAIN_TARGET,
                                           "RW": False, "Propagation": "rprivate"})
            maintenance.h.inspect.return_value = replacement
            maintenance.stop_proxy.side_effect = [True, False]
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            self.assertEqual(maintenance.compose_up.call_count, 1)
            self.assertEqual(maintenance.write_compose.call_count, 1)
            maintenance.remove_drain.assert_not_called()

    def test_concurrent_replacement_is_not_signalled_during_rollback(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.ready.side_effect = RuntimeError("not verified")
            replacement = self.nginx()
            replacement["Id"] = "different"
            replacement["Image"] = "unexpected"
            maintenance.h.inspect.return_value = replacement
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            self.assertEqual(maintenance.stop_proxy.call_count, 1)
            self.assertEqual(maintenance.disable_restart.call_count, 1)
            self.assertEqual(maintenance.compose_up.call_count, 1)
            maintenance.remove_drain.assert_not_called()

    def test_ambiguous_restart_update_before_signal_restores_exact_live_policy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.disable_restart.side_effect = RuntimeError("unknown update result")
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            maintenance.restore_restart.assert_called_once()
            maintenance.stop_proxy.assert_not_called()
            maintenance.compose_up.assert_not_called()
            maintenance.remove_drain.assert_called_once()

    def test_partial_compose_write_still_attempts_original_compose_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = self.fixture(directory)
            maintenance.write_compose.side_effect = [RuntimeError("partial first-file write"), None]
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            self.assertEqual(maintenance.write_compose.call_count, 2)
            self.assertEqual(maintenance.write_compose.call_args_list[-1].args[0], ("base", "override"))
            maintenance.stop_proxy.assert_not_called()
            maintenance.compose_up.assert_not_called()
            maintenance.remove_drain.assert_called_once()

    def test_failed_recreation_restores_only_proxy_and_original_compose(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.ready.side_effect = [RuntimeError("not ready"), None]
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            self.assertEqual(maintenance.write_compose.call_args_list[-1].args[0], ("base", "override"))
            self.assertEqual(maintenance.compose_up.call_count, 2)
            self.assertFalse(maintenance.ready.call_args_list[-1].kwargs["directory"])
            maintenance.remove_drain.assert_called_once()

    def test_ready_proxy_is_not_recreated_if_release_or_audit_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.remove_drain.side_effect = RuntimeError("owned drain requires reconciliation")
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            self.assertEqual(maintenance.compose_up.call_count, 1)
            self.assertEqual(maintenance.write_compose.call_count, 1)
            self.assertTrue((Path(directory)/"proxy-mount-recovery-required.json").exists())

    def test_failed_baseline_restore_keeps_owned_drain(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "check_resolved_compose"):
            maintenance = self.fixture(directory)
            maintenance.ready.side_effect = [RuntimeError("candidate"), RuntimeError("baseline")]
            with self.assertRaises(RuntimeError):
                maintenance.maintain()
            maintenance.remove_drain.assert_not_called()
            self.assertTrue((Path(directory)/"proxy-mount-recovery-required.json").exists())

    def test_partial_drain_write_cleans_only_its_own_inode_and_contents(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(proxy, "DRAIN", Path(directory)/"DRAIN"):
            maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
            with patch.object(proxy.os, "fsync", side_effect=OSError("sync")):
                with self.assertRaises(OSError):
                    maintenance.create_drain()
            maintenance.remove_drain()
            self.assertFalse(proxy.DRAIN.exists())

    def test_restart_suppression_targets_exact_original_id_and_reads_policy_back(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
            original = self.nginx()
            changed = copy.deepcopy(original)
            changed["HostConfig"]["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
            maintenance._runtime = Mock(h=Mock(inspect=Mock(side_effect=[original, changed])))
            maintenance.run = Mock(return_value="")
            maintenance.disable_restart(original)
            maintenance.run.assert_called_once_with(["docker", "update", "--restart=no", "original"])

    def test_restart_policy_restore_rejects_changed_proxy_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
            original = self.nginx()
            changed = copy.deepcopy(original)
            changed["Id"] = "operator-replacement"
            maintenance._runtime = Mock(h=Mock(inspect=Mock(return_value=changed)))
            maintenance.run = Mock(return_value="")
            with self.assertRaises(RuntimeError):
                maintenance.restore_restart(original)
            maintenance.run.assert_not_called()

    def test_actual_graceful_timeout_uses_only_sigquit_and_never_forces_kill(self):
        class Clock:
            value = 0
            def now(self):
                return self.value
            def sleep(self, value):
                self.value += 20
        with tempfile.TemporaryDirectory() as directory:
            maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
            original = self.nginx()
            guarded = copy.deepcopy(original)
            guarded["HostConfig"]["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
            maintenance._runtime = Mock(h=Mock(inspect=Mock(return_value=guarded)))
            maintenance.run = Mock(return_value="")
            clock = Clock()
            with patch.object(proxy.time, "monotonic", side_effect=clock.now), patch.object(proxy.time, "sleep", side_effect=clock.sleep):
                self.assertFalse(maintenance.stop_proxy({"nginx_inspect": original}))
            maintenance.run.assert_called_once_with(["docker", "kill", "--signal", "SIGQUIT", "original"])

    def test_existing_phase_marker_blocks_replay_before_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
            (Path(directory)/"proxy-mount-started.json").write_text("{}")
            with self.assertRaises(RuntimeError):
                maintenance.preflight()

    def canary_fixture(self, directory):
        maintenance = proxy.ProxyMountMaintenance(root=Path(directory))
        original = self.nginx()
        canary = copy.deepcopy(original)
        canary["Id"] = "b"*64
        canary["Config"]["Labels"] = {"xtai.issue187.proxy-check": maintenance._invocation}
        canary["HostConfig"]["PortBindings"] = {}
        canary["Mounts"][0].update(Source=str(proxy.CONFIG_DIR), Destination=proxy.DIRECTORY_TARGET)
        canary["Mounts"].append({"Type": "bind", "Source": str(proxy.STATIC_MAIN), "Destination": proxy.MAIN_TARGET,
                                 "RW": False, "Propagation": "rprivate"})
        maintenance._runtime = Mock(h=Mock(inspect=Mock(return_value=canary)))
        loaded = "# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/conf.d/*.conf; }\n" + \
                 "# configuration file /etc/nginx/conf.d/default.conf:\nserver {}\n"
        before = {"nginx_inspect": original, "default": "server {}\n", "loaded": loaded}
        outputs = ["b"*64, "", "", "", "main-new", "server {}\n", proxy.static_main(loaded), ""]
        maintenance.run = Mock(side_effect=outputs)
        return maintenance, before, canary

    def test_canary_uses_every_mount_and_network_and_no_host_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance, before, _ = self.canary_fixture(directory)
            maintenance.test_canary(before, "main-new")
            create = maintenance.run.call_args_list[0].args[0]
            self.assertEqual(create[:2], ["docker", "create"])
            self.assertNotIn("-p", create)
            self.assertNotIn("--publish", create)
            self.assertIn(before["nginx_inspect"]["Image"], create)
            mounts = [create[index+1] for index, value in enumerate(create) if value == "--mount"]
            self.assertEqual(len(mounts), 3)
            self.assertTrue(all(",readonly" in value for value in mounts))
            self.assertIn(["docker", "network", "connect", "internal", "b"*64],
                          [call.args[0] for call in maintenance.run.call_args_list])
            self.assertEqual(maintenance.run.call_args_list[-1].args[0], ["docker", "rm", "-f", "b"*64])
            self.assertTrue((Path(directory)/"proxy-mount-canary-verified.json").exists())

    def test_canary_extra_host_ports_fail_before_nginx_test_and_cleanup_only_owned_id(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance, before, canary = self.canary_fixture(directory)
            canary["HostConfig"]["PortBindings"] = {"80/tcp": [{"HostPort": "18000"}]}
            maintenance.run.side_effect = ["b"*64, "", "", ""]
            with self.assertRaises(RuntimeError):
                maintenance.test_canary(before, "main-new")
            commands = [call.args[0] for call in maintenance.run.call_args_list]
            self.assertFalse(any(command[:2] == ["docker", "exec"] for command in commands))
            self.assertEqual(commands[-1], ["docker", "rm", "-f", "b"*64])
            self.assertFalse((Path(directory)/"proxy-mount-canary-verified.json").exists())

    def test_canary_allows_environment_permutation_after_full_key_value_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance, before, canary = self.canary_fixture(directory)
            before["nginx_inspect"]["Config"]["Env"] = ["PATH=/bin", "KEEP=same", "EMPTY="]
            canary["Config"]["Env"] = ["EMPTY=", "KEEP=same", "PATH=/bin"]
            maintenance.test_canary(before, "main-new")
            self.assertTrue((Path(directory)/"proxy-mount-canary-verified.json").exists())

    def test_canary_rejects_changed_value_or_duplicate_environment_key(self):
        for canary_env in [["PRIVATE=changed"], ["PRIVATE=test", "PRIVATE=test"]]:
            with tempfile.TemporaryDirectory() as directory:
                maintenance, before, canary = self.canary_fixture(directory)
                canary["Config"]["Env"] = canary_env
                maintenance.run.side_effect = ["b"*64, "", "", ""]
                with self.assertRaises(RuntimeError):
                    maintenance.test_canary(before, "main-new")
                self.assertFalse((Path(directory)/"proxy-mount-canary-verified.json").exists())


if __name__ == "__main__":
    unittest.main()
