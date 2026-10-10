"""Offline deployment-boundary regressions; no Docker, SSH or paid calls."""
import copy
import hashlib
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

MODULE = pathlib.Path(__file__).with_name("deploy_image_fixes.py")
spec = importlib.util.spec_from_file_location("image_fixes190", MODULE)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

OLD_ID = "a" * 64
OLD_IMAGE = "sha256:" + "b" * 64
NEW_IMAGE = "sha256:" + "c" * 64
OPERATION = "d" * 32


def inspected(name=h.NATIVE):
    return {
        "Id": OLD_ID, "Image": OLD_IMAGE, "Name": "/" + name,
        "State": {"Running": True},
        "Config": {
            "Image": "old:tag", "Env": ["TZ=Asia/Shanghai", "SECRET=private-value"],
            "User": "10001:10001", "Entrypoint": ["python", "/app/app.py"],
            "Cmd": None, "WorkingDir": "/app", "Hostname": "existing-host",
            "Labels": {"existing": "preserve"}, "Volumes": {},
            "Healthcheck": {"Test": ["CMD", "true"]},
        },
        "HostConfig": {
            "Binds": ["/safe/config.json:/run/secrets/config.json:ro"],
            "NetworkMode": "app-net", "Memory": 512 * 1024 * 1024,
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "ReadonlyRootfs": True, "PortBindings": {}, "Mounts": [],
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [{"Type": "bind", "Source": "/safe/config.json", "Destination": "/run/secrets/config.json", "RW": False, "Propagation": "rprivate"}],
        "NetworkSettings": {"Networks": {"app-net": {"Aliases": [name, OLD_ID, OLD_ID[:12]], "IPAddress": "172.18.0.5", "IPAMConfig": None, "DriverOpts": None, "Links": None}}},
    }


class DeploymentBoundaryTests(unittest.TestCase):
    def test_runtime_mount_list_order_is_not_configuration_drift(self):
        before = inspected()
        before["HostConfig"]["Binds"].append("/safe/data:/data:rw")
        before["Mounts"].append({"Type": "bind", "Source": "/safe/data", "Destination": "/data", "RW": True, "Propagation": "rprivate"})
        reordered = copy.deepcopy(before)
        reordered["Mounts"].reverse()
        h.assert_fresh_baseline(before, reordered)
        config = h.clone_create_config(before, NEW_IMAGE, OPERATION)
        after = copy.deepcopy(reordered)
        after.update(Id="e" * 64, Image=NEW_IMAGE)
        after["Config"] = {k: v for k, v in config.items() if k not in {"HostConfig", "NetworkingConfig"}}
        after["NetworkSettings"]["Networks"]["app-net"]["Aliases"] = [h.NATIVE, "e" * 12]
        h.assert_replacement(before, after, NEW_IMAGE, OPERATION)
        after["Mounts"][0]["Source"] = "/different/data"
        with self.assertRaises(h.DeploymentError):
            h.assert_replacement(before, after, NEW_IMAGE, OPERATION)

    def test_only_four_exact_targets_are_allowed(self):
        self.assertEqual(len(h.TARGETS), 4)
        self.assertNotIn(h.IMAGE_GATEWAY, h.TARGETS)
        with self.assertRaises(h.DeploymentError):
            h.clone_create_config(inspected(h.IMAGE_GATEWAY), NEW_IMAGE, OPERATION)

    def test_clone_preserves_every_runtime_field_except_image_and_operation_label(self):
        before = inspected()
        expected = copy.deepcopy(before["Config"])
        expected["Image"] = NEW_IMAGE
        expected["Labels"]["com.aixingtuyun.image-fixes-operation"] = OPERATION
        result = h.clone_create_config(before, NEW_IMAGE, OPERATION)
        host = result.pop("HostConfig")
        networks = result.pop("NetworkingConfig")
        self.assertEqual(result, expected)
        self.assertEqual(host, before["HostConfig"])
        self.assertEqual(networks["EndpointsConfig"]["app-net"]["Aliases"], [h.NATIVE])
        self.assertEqual(before, inspected(), "clone must never mutate the saved rollback identity")

    def test_clone_preserves_static_endpoint_settings_without_copying_dynamic_ip(self):
        before = inspected()
        network = before["NetworkSettings"]["Networks"]["app-net"]
        network.update(IPAMConfig={"IPv4Address": "172.18.0.9"}, DriverOpts={"x": "y"}, Links=["service:alias"])
        endpoints = h.clone_create_config(before, NEW_IMAGE, OPERATION)["NetworkingConfig"]["EndpointsConfig"]
        self.assertEqual(endpoints["app-net"]["IPAMConfig"], network["IPAMConfig"])
        self.assertNotIn("IPAddress", endpoints["app-net"])

    def test_duplicate_or_malformed_environment_is_refused(self):
        for env in (["A=1", "A=2"], ["malformed"], ["A=1\nB=2"]):
            before = inspected(); before["Config"]["Env"] = env
            with self.subTest(env=env), self.assertRaises(h.DeploymentError):
                h.clone_create_config(before, NEW_IMAGE, OPERATION)

    def test_unrepresented_persistent_mount_is_refused_instead_of_new_empty_volume(self):
        before = inspected()
        before["Config"]["Volumes"] = {"/data": {}}
        before["Mounts"].append({"Type": "volume", "Name": "owned-existing", "Source": "/var/lib/docker/volumes/owned-existing/_data", "Destination": "/data", "RW": True})
        with self.assertRaises(h.DeploymentError):
            h.clone_create_config(before, NEW_IMAGE, OPERATION)

    def test_named_volume_explicitly_bound_is_preserved(self):
        before = inspected()
        before["HostConfig"]["Binds"].append("owned-existing:/data:rw")
        before["Mounts"].append({"Type": "volume", "Name": "owned-existing", "Source": "/var/lib/docker/volumes/owned-existing/_data", "Destination": "/data", "RW": True})
        self.assertEqual(h.clone_create_config(before, NEW_IMAGE, OPERATION)["HostConfig"], before["HostConfig"])

    def test_mismatched_mount_source_or_write_flag_is_refused_before_creation(self):
        for bind in ("/different.json:/run/secrets/config.json:ro", "/safe/config.json:/run/secrets/config.json:rw"):
            before = inspected(); before["HostConfig"]["Binds"] = [bind]
            with self.subTest(bind=bind), self.assertRaises(h.DeploymentError):
                h.clone_create_config(before, NEW_IMAGE, OPERATION)

    def test_source_label_is_explicitly_accounted_for_without_accepting_arbitrary_label_change(self):
        before = inspected()
        labels = {"existing": "preserve", "com.aixingtuyun.image-fixes-policy-sha256": "f" * 64}
        clone = h.clone_create_config(before, NEW_IMAGE, OPERATION, image_labels=labels)
        self.assertEqual(clone["Labels"]["com.aixingtuyun.image-fixes-policy-sha256"], "f" * 64)
        for invalid in ({"existing": "overwritten"}, {"arbitrary": "change"}, {"com.aixingtuyun.image-fixes-policy-sha256": "mutable"}):
            with self.subTest(invalid=invalid), self.assertRaises(h.DeploymentError):
                h.clone_create_config(before, NEW_IMAGE, OPERATION, image_labels=invalid)

    def test_fresh_identity_and_configuration_drift_are_fail_closed(self):
        baseline = inspected()
        for mutate in (
            lambda x: x.update(Id="e" * 64),
            lambda x: x.update(Image=NEW_IMAGE),
            lambda x: x["Config"].update(User="0"),
            lambda x: x["Config"]["Env"].append("NEW=unexpected"),
            lambda x: x["HostConfig"].update(Memory=0),
            lambda x: x["Mounts"][0].update(RW=True),
            lambda x: x["State"].update(Running=False),
            lambda x: x["NetworkSettings"]["Networks"]["app-net"].update(IPAddress="172.18.0.99"),
        ):
            current = copy.deepcopy(baseline); mutate(current)
            with self.subTest(current=current), self.assertRaises(h.DeploymentError):
                h.assert_fresh_baseline(baseline, current)
        h.assert_fresh_baseline(baseline, copy.deepcopy(baseline))

    def test_replacement_verification_requires_exact_private_config_and_mounts(self):
        before = inspected()
        config = h.clone_create_config(before, NEW_IMAGE, OPERATION)
        after = copy.deepcopy(before)
        after.update(Id="e" * 64, Image=NEW_IMAGE)
        after["Config"] = {k: v for k, v in config.items() if k not in {"HostConfig", "NetworkingConfig"}}
        after["NetworkSettings"]["Networks"]["app-net"]["Aliases"] = [h.NATIVE, "e" * 12]
        after["NetworkSettings"]["Networks"]["app-net"]["IPAddress"] = "172.18.0.9"
        h.assert_replacement(before, after, NEW_IMAGE, OPERATION)
        after["Config"]["Env"][0] = "TZ=UTC"
        with self.assertRaises(h.DeploymentError):
            h.assert_replacement(before, after, NEW_IMAGE, OPERATION)

    def test_adapter_dockerfiles_pin_base_and_preserve_user_and_entrypoint(self):
        for name in h.ADAPTERS:
            text = h.adapter_dockerfile(name, OLD_IMAGE, "a" * 64)
            self.assertIn("FROM " + OLD_IMAGE, text)
            self.assertIn("COPY app.py /app/app.py", text)
            self.assertNotIn("--chmod", text)
            self.assertNotIn("ENTRYPOINT", text)
            self.assertNotIn("USER ", text)
            self.assertNotIn("pip ", text)
            self.assertEqual("image_io.py" in text, name != h.BANANA)
        with self.assertRaises(h.DeploymentError):
            h.adapter_dockerfile(h.NATIVE, OLD_IMAGE, "a" * 64)

    def test_mutable_image_tag_and_untrusted_operation_are_refused(self):
        for image, op in (("latest:tag", OPERATION), (NEW_IMAGE, "../escape")):
            with self.assertRaises(h.DeploymentError):
                h.clone_create_config(inspected(), image, op)

    def test_native_dockerfile_changes_only_binary_on_exact_runtime_base(self):
        text = h.native_dockerfile(OLD_IMAGE, "sha256:" + "f" * 64, "a" * 64)
        self.assertIn("FROM " + OLD_IMAGE, text)
        self.assertIn("COPY --from=builder /new-api /new-api", text)
        self.assertIn("COPY native-source/ /issue190-source/", text)
        self.assertIn("test ! -e /issue190-source", text)
        self.assertIn("common.Version=$(cat VERSION)", text)
        self.assertIn("&& test -x /new-api", text)
        self.assertNotIn("--chmod", text)
        self.assertNotIn("COPY native-source/ /build/", text)
        self.assertIn("go test ./service -count=1", text)
        self.assertNotIn("bun ", text)
        self.assertNotIn("npm ", text)

    def test_busy_unknown_or_stale_idle_evidence_cannot_authorize_stopping(self):
        row = {"image_jobs_active": 0, "banana_active_requests": 0, "nody_handler_threads": 1, "image25_handler_threads": 1, "native_reading": 0, "native_writing": 1}
        evidence = {"operation_id": OPERATION, "maintenance_active": True, "owned_image_drain": True, "internal_submitters_excluded": True, "checked_at": 1000, "samples": [copy.deepcopy(row) for _ in range(3)]}
        h.assert_idle_evidence(evidence, OPERATION, now=1002)
        for key, value in (("image_jobs_active", 1), ("banana_active_requests", 1), ("nody_handler_threads", None), ("image25_handler_threads", 2), ("native_writing", 2)):
            invalid = copy.deepcopy(evidence); invalid["samples"][-1][key] = value
            with self.subTest(key=key), self.assertRaises(h.DeploymentError):
                h.assert_idle_evidence(invalid, OPERATION, now=1002)
        with self.assertRaises(h.DeploymentError):
            h.assert_idle_evidence(evidence, OPERATION, now=1020)
        evidence["internal_submitters_excluded"] = False
        with self.assertRaises(h.DeploymentError):
            h.assert_idle_evidence(evidence, OPERATION, now=1002)

    def test_health_200_is_not_substituted_for_active_request_evidence(self):
        evidence = {"operation_id": OPERATION, "maintenance_active": True, "owned_image_drain": True, "checked_at": 1000, "samples": [{"health_status": 200} for _ in range(3)]}
        with self.assertRaises(h.DeploymentError):
            h.assert_idle_evidence(evidence, OPERATION, now=1001)

    def test_maintenance_location_has_no_submit_headers_and_does_not_gate_video(self):
        text = h.maintenance_location(OPERATION)
        self.assertIn("not_submitted always", text)
        self.assertIn("return 503", text)
        self.assertNotIn("videos", text)
        self.assertNotIn("callbacks", text)
        self.assertNotIn("location /", text)
        self.assertIn("v1/(edits|images/", text)
        self.assertIn("pg/chat/completions", text)
        self.assertIn("api/channel/test(/[0-9]+)?", text)


class SourcePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.live = self.root / "production-source"
        (self.live / "service").mkdir(parents=True)
        (self.live / "web" / "default" / "dist").mkdir(parents=True)
        (self.live / "web" / "classic" / "dist").mkdir(parents=True)
        (self.live / "service" / "image_route_policy.go").write_bytes(b"old policy")
        (self.live / "model").mkdir()
        (self.live / "model" / "pricing.go").write_bytes(b"exact production pricing")
        (self.live / "web" / "default" / "dist" / "index.html").write_bytes(b"exact production frontend")
        (self.live / "web" / "classic" / "dist" / "index.html").write_bytes(b"exact production classic frontend")
        (self.live / "VERSION").write_bytes(b"v-existing-production")
        self.policy = self.root / "reviewed.go"
        self.policy.write_bytes(b"reviewed policy")
        self.new_hash = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        self.old_hash = hashlib.sha256(b"old policy").hexdigest()
        self.tree = h.tree_manifest(self.live)
        self.assets = {name: digest for name, digest in self.tree.items() if name.startswith("web/")}

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, destination):
        with mock.patch.object(h, "LIVE_NATIVE_SOURCE", self.live), mock.patch.object(h, "NATIVE_POLICY_BASELINE_SHA256", self.old_hash):
            return h.prepare_native_source(destination, self.policy, self.tree, self.new_hash, assets_manifest=self.assets)

    def test_exact_native_source_backport_does_not_overwrite_marketplace_or_frontend(self):
        target = self.root / "owned-candidate"
        result = self.prepare(target)
        self.assertEqual((target / "service" / "image_route_policy.go").read_bytes(), b"reviewed policy")
        self.assertEqual((target / "model" / "pricing.go").read_bytes(), b"exact production pricing")
        self.assertEqual((target / "web" / "default" / "dist" / "index.html").read_bytes(), b"exact production frontend")
        self.assertEqual((target / "web" / "classic" / "dist" / "index.html").read_bytes(), b"exact production classic frontend")
        self.assertEqual((target / "VERSION").read_bytes(), b"v-existing-production")
        self.assertEqual(result["changed_paths"], ["service/image_route_policy.go"])
        self.assertEqual((self.live / "service" / "image_route_policy.go").read_bytes(), b"old policy")

    def test_source_drift_or_reviewed_policy_drift_aborts_before_copy(self):
        for change in (self.live / "model" / "pricing.go", self.policy):
            previous = change.read_bytes(); change.write_bytes(b"changed after freeze")
            target = self.root / "must-not-exist"
            with self.subTest(change=change), self.assertRaises(h.DeploymentError):
                self.prepare(target)
            self.assertFalse(target.exists())
            change.write_bytes(previous)

    def test_missing_or_unverified_embedded_frontend_aborts_before_copy(self):
        with mock.patch.object(h, "LIVE_NATIVE_SOURCE", self.live), mock.patch.object(h, "NATIVE_POLICY_BASELINE_SHA256", self.old_hash):
            with self.assertRaises(h.DeploymentError):
                h.prepare_native_source(self.root / "no-proof", self.policy, self.tree, self.new_hash)
        path = self.live / "web" / "classic" / "dist" / "index.html"
        path.unlink()
        self.tree = h.tree_manifest(self.live)
        with self.assertRaises(h.DeploymentError): self.prepare(self.root / "missing-assets")
        self.assertFalse((self.root / "missing-assets").exists())

    def test_existing_destination_and_destination_inside_live_tree_are_refused(self):
        target = self.root / "existing"; target.mkdir(); (target / "keep").write_text("user-owned")
        with self.assertRaises(h.DeploymentError): self.prepare(target)
        self.assertEqual((target / "keep").read_text(), "user-owned")
        with self.assertRaises(h.DeploymentError): self.prepare(self.live / "candidate")

    def test_adapter_context_checks_exact_helper_and_copies_no_secret_file(self):
        source = self.root / "adapter"; source.mkdir()
        (source / "app.py").write_bytes(b"reviewed adapter")
        (source / "image_io.py").write_bytes(b"exact helper")
        (source / "secret.key").write_bytes(b"must not be copied")
        app_hash = hashlib.sha256(b"reviewed adapter").hexdigest()
        io_hash = hashlib.sha256(b"exact helper").hexdigest()
        with mock.patch.object(h, "IMAGE_IO_SHA256", io_hash), mock.patch.object(h.os, "chmod", wraps=h.os.chmod) as chmod:
            result = h.prepare_adapter_context(h.NODY, source, self.root / "context", OLD_IMAGE, app_hash)
        chmod.assert_any_call(self.root / "context" / "app.py", 0o644)
        chmod.assert_any_call(self.root / "context" / "image_io.py", 0o644)
        self.assertEqual(set(p.name for p in (self.root / "context").iterdir()), {"app.py", "image_io.py", "Dockerfile"})
        self.assertEqual(result["image_io_sha256"], io_hash)
        (source / "image_io.py").write_bytes(b"unreviewed helper")
        with mock.patch.object(h, "IMAGE_IO_SHA256", io_hash), self.assertRaises(h.DeploymentError):
            h.prepare_adapter_context(h.NODY, source, self.root / "bad-context", OLD_IMAGE, app_hash)
        self.assertFalse((self.root / "bad-context").exists())

    def test_helper_does_not_implement_automatic_swap_or_paid_submission(self):
        self.assertFalse(hasattr(h, "promote"))
        self.assertFalse(hasattr(h, "create_paid_job"))
        self.assertFalse(hasattr(h, "restore_database"))


if __name__ == "__main__":
    unittest.main()
