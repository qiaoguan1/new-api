"""Mock-only native release regressions; never contact Docker, HTTP or a database."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("native_promote190", HERE / "promote_native.py")
n = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(n)


def native_fixture():
    """A fully represented private native instance, with no external credentials."""
    identity = "a" * 64
    return {"Id": identity, "Image": "sha256:" + "b" * 64,
            "Name": "/" + n.h.NATIVE, "RestartCount": 0,
            "State": {"Running": True, "Restarting": False, "Status": "running", "Pid": 111,
                      "StartedAt": "2026-10-10T01:00:00Z"},
            "Config": {"Env": ["QUOTA_DB_AUTHORITATIVE=true", "BATCH_UPDATE_ENABLED=false"],
                       "Labels": {}, "User": "10001", "Entrypoint": ["/new-api"],
                       "Cmd": ["--log-dir", "/logs"], "WorkingDir": "/data"},
            "HostConfig": {"NetworkMode": "app-net", "Binds": ["/opt/existing:/data:rw"],
                           "PortBindings": {}, "RestartPolicy": {"Name": "unless-stopped"},
                           "SecurityOpt": ["no-new-privileges:true"]},
            "Mounts": [{"Type": "bind", "Source": "/opt/existing", "Destination": "/data", "RW": True}],
            "NetworkSettings": {"Networks": {"app-net": {"Aliases": [identity, identity[:12], "new-api"],
                                                          "IPAddress": "172.18.0.9"}}}}


def manifest_fixture():
    """A quoted source/UI attestation, not an upstream request or a tariff."""
    return {"operation": n.OP, "before_id": "a" * 64, "before_image": "sha256:" + "b" * 64,
            "candidate_image": n.CANDIDATE_IMAGE, "policy_sha256": n.POLICY_SHA256,
            "binary_sha256": "c" * 64, "pricing_source_sha256": n.PRICING_SHA256,
            "original_main_sha256": n.MAIN_SHA256,
            "original_nginx_sha256": "1" * 64, "gated_nginx_sha256": "2" * 64,
            "dependency_evidence_sha256": "3" * 64, "route_audit_sha256": "4" * 64,
            "admission_coverage": "operator-reviewed-all-native-mutating-and-generation-entrypoints",
            "version": "v0.fixture", "public_assets": {"/": "d" * 64, "/assets/index.js": "e" * 64}}


class RootOwnedGateFixturePath(type(Path())):
    """Model a reviewed Linux0600 artifact without changing production checks.

    Windows cannot express the required POSIX owner/mode via chmod. Only this
    mock orchestration input has synthetic owner bits; all real path/read/type
    behavior remains in pathlib and production uses an ordinary Path.
    """

    def stat(self, *args, **kwargs):
        values = list(super().stat(*args, **kwargs))
        values[0] = (values[0] & ~0o7777) | 0o600
        values[4] = 0
        return n.os.stat_result(values)


class NativeGuardTests(unittest.TestCase):
    def test_approval_expiring_before_drain_never_writes_admission_or_stops_native(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory)
            with stack, patch.object(n.time, "time", side_effect=[1100, 1201]):
                with self.assertRaises(n.h.DeploymentError): n.run_locked()
            self.assertFalse(drain.exists())
            self.assertEqual(conf.read_bytes(), b"untouched existing nginx")
            self.assertTrue(containers["a" * 64]["State"]["Running"])
            self.assertFalse(any("/kill" in path or "/containers/create" in path for path, _, _ in operations))

    def test_approval_expiring_before_gate_restores_owned_drain_without_native_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory)
            with stack, patch.object(n.time, "time", side_effect=[1100, 1100, 1201]):
                with self.assertRaises(n.h.DeploymentError): n.run_locked()
            self.assertFalse(drain.exists())
            self.assertEqual(conf.read_bytes(), b"untouched existing nginx")
            self.assertTrue(containers["a" * 64]["State"]["Running"])
            self.assertFalse(any("/kill" in path or "/containers/create" in path for path, _, _ in operations))
            self.assertEqual(journal[-1]["phase"], "rolled_back")

    def test_approval_expiring_before_term_reopens_without_native_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory)
            with stack, patch.object(n.time, "time", side_effect=[1100, 1100, 1100, 1201]):
                with self.assertRaises(n.h.DeploymentError): n.run_locked()
            self.assertFalse(drain.exists())
            self.assertEqual(conf.read_bytes(), b"untouched existing nginx")
            self.assertTrue(containers["a" * 64]["State"]["Running"])
            self.assertFalse(any("/kill" in path or "/containers/create" in path for path, _, _ in operations))
            self.assertEqual(journal[-1]["phase"], "rolled_back")

    def test_empty_version_requires_explicit_matching_attestation(self):
        manifest = manifest_fixture(); manifest["version"] = ""
        with self.assertRaises(n.h.DeploymentError): n.validate_manifest(manifest, native_fixture())
        manifest["explicit_empty_version_attestation"] = {"serving_status_version": "", "frozen_source_version_sha256": hashlib.sha256(b"").hexdigest()}
        n.validate_manifest(manifest, native_fixture())
        for invalid in (None, {}, {"serving_status_version": "v1", "frozen_source_version_sha256": hashlib.sha256(b"").hexdigest()},
                        {"serving_status_version": "", "frozen_source_version_sha256": "f" * 64}):
            manifest["explicit_empty_version_attestation"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(n.h.DeploymentError):
                n.validate_manifest(manifest, native_fixture())

    def test_empty_version_is_checked_against_live_status_and_source(self):
        manifest = manifest_fixture(); manifest["version"] = ""
        manifest["explicit_empty_version_attestation"] = {"serving_status_version": "", "frozen_source_version_sha256": hashlib.sha256(b"").hexdigest()}
        payload = {"success": True, "data": {"quota_db_authoritative": True, "enable_batch_update": False, "version": ""}}
        manifest["public_assets"] = {"/": hashlib.sha256(b"index").hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "VERSION").write_bytes(b"")
            with patch.object(n.h, "LIVE_NATIVE_SOURCE", root), patch.object(n.a, "inspect", return_value=native_fixture()), \
                 patch.object(n, "native_get", side_effect=lambda identity, path: (200, json.dumps(payload).encode() if path == "/api/status" else b"index")):
                n.verify_native_http("a" * 64, manifest)
                payload["data"]["version"] = "drift"
                with self.assertRaises(n.h.DeploymentError): n.verify_native_http("a" * 64, manifest)
                payload["data"]["version"] = ""
                (root / "VERSION").write_bytes(b"changed")
                with self.assertRaises(n.h.DeploymentError): n.verify_native_http("a" * 64, manifest)

    def release_fixture(self, directory, *, create_failure=None, reopen_failure=False, disconnect_failure=False):
        """Exercise real run/rollback orchestration against an in-memory Docker model."""
        root = Path(directory); conf = root / "nginx.conf"; drain = root / "DRAIN"
        original = b"untouched existing nginx"; conf.write_bytes(original)
        gated = b"# issue190-native-" + n.OP.encode() + b"\nlisten 127.0.0.1:18090; reviewed-wide-gate"
        gate = RootOwnedGateFixturePath(root / "native-nginx.gated.conf"); gate.write_bytes(gated)
        old = native_fixture(); old["Config"]["Image"] = old["Image"]
        old["NetworkSettings"]["Networks"]["internal"] = {"Aliases": ["new-api"], "IPAddress": "172.20.0.8"}
        manifest = manifest_fixture()
        manifest.update(original_nginx_sha256=hashlib.sha256(original).hexdigest(),
                        gated_nginx_sha256=hashlib.sha256(gated).hexdigest())
        containers = {old["Id"]: copy.deepcopy(old)}
        operations = []; journal = []
        approval = {"operation": n.OP, "decision": "A", "scope": n.APPROVAL_SCOPE,
                    "source": "direct-human-user-reply", "approved_at": 1000, "expires_at": 1200}

        def inspected(identity):
            if identity in containers: return copy.deepcopy(containers[identity])
            found = next((item for item in containers.values() if item["Name"] == "/" + identity), None)
            if found: return copy.deepcopy(found)
            return dict(copy.deepcopy(old), Id="e" * 64, Name="/" + identity)

        def optional(name):
            return next((copy.deepcopy(item) for item in containers.values() if item["Name"] == "/" + name), None)

        def docker(path, method="GET", body=None):
            operations.append((path, method, copy.deepcopy(body)))
            if path.startswith("/images/"):
                return {"Id": n.CANDIDATE_IMAGE, "Config": {"Labels": {"com.aixingtuyun.image-fixes-policy-sha256": n.POLICY_SHA256}}}
            if path.startswith("/containers/create"):
                if create_failure == "absent": raise TimeoutError("create response unknown, absent instance")
                config = {key: value for key, value in body.items() if key not in {"HostConfig", "NetworkingConfig"}}
                containers["d" * 64] = {"Id": "d" * 64, "Image": n.CANDIDATE_IMAGE, "Name": "/" + n.h.NATIVE,
                    "Config": copy.deepcopy(config), "HostConfig": copy.deepcopy(body["HostConfig"]),
                    "Mounts": copy.deepcopy(old["Mounts"]), "RestartCount": 0,
                    "State": {"Running": False, "Restarting": False, "Status": "created", "Pid": 0,
                              "StartedAt": "0001-01-01T00:00:00Z"},
                    "NetworkSettings": {"Networks": {name: {**copy.deepcopy(endpoint), "IPAddress": "172.18.0.19"}
                                                       for name, endpoint in body["NetworkingConfig"]["EndpointsConfig"].items()}}}
                if create_failure == "lost": raise TimeoutError("lost response after exact create")
                return {"Id": "d" * 64}
            if path.startswith("/networks/"):
                network = path.split("/")[2]; identity = body["Container"]
                if path.endswith("/disconnect"):
                    if disconnect_failure and network == "internal": raise TimeoutError("partial network disconnect")
                    containers[identity]["NetworkSettings"]["Networks"].pop(network, None)
                else:
                    containers[identity]["NetworkSettings"]["Networks"][network] = {**copy.deepcopy(body["EndpointConfig"]), "IPAddress": "172.18.0.20"}
                return {}
            identity = path.split("/")[2].split("?")[0]
            if "/kill?signal=SIGTERM" in path:
                containers[identity]["State"].update(Running=False, Restarting=False, Status="exited", Pid=0)
            elif "/rename?name=" in path: containers[identity]["Name"] = "/" + path.split("name=")[1]
            elif path.endswith("/start"):
                containers[identity]["State"].update(Running=True, Restarting=False, Status="running", Pid=222,
                    StartedAt="2026-10-10T02:00:00Z")
            else: raise AssertionError("Unexpected mock Docker mutation")
            return {}

        def write_conf(content, expected):
            self.assertEqual(conf.read_bytes(), expected)
            if reopen_failure and content == original and expected == gated:
                raise TimeoutError("lost reopen response")
            conf.write_bytes(content)

        stack = ExitStack()
        for target, name, value in (
            (n, "ROOT", root), (n, "STATE", root / "native-rollout.json"), (n, "CONF", conf),
            (n, "DRAIN", drain), (n, "GATED_CONF", gate), (n, "MANIFEST", root / "native-candidate.json"),
            (n, "APPROVAL", root / "approval.json")):
            stack.enter_context(patch.object(target, name, value))
        stack.enter_context(patch.object(n.os, "O_NOFOLLOW", 0, create=True))
        stack.enter_context(patch.object(n.time, "time", return_value=1100))
        stack.enter_context(patch.object(n, "private_json", side_effect=lambda path: approval if path == n.APPROVAL else manifest))
        stack.enter_context(patch.object(n, "save", side_effect=lambda state: journal.append(copy.deepcopy(state))))
        stack.enter_context(patch.object(n.a, "inspect", side_effect=inspected))
        stack.enter_context(patch.object(n.a, "inspect_optional", side_effect=optional))
        stack.enter_context(patch.object(n.a, "docker", side_effect=docker))
        stack.enter_context(patch.object(n.a, "write_conf", side_effect=write_conf))
        stack.enter_context(patch.object(n.a, "command", return_value=(manifest["binary_sha256"] + "  /new-api\n").encode()))
        for name in ("verify_native_http", "verify_inert_version", "assert_video_unchanged", "assert_deployment_evidence", "assert_owned_admission", "await_native", "assert_replacement_idle"):
            stack.enter_context(patch.object(n, name))
        stack.enter_context(patch.object(n.a, "assert_internal_submitters"))
        stack.enter_context(patch.object(n, "assert_quiet", return_value={"samples": [0, 0, 0]}))
        return stack, containers, operations, journal, conf, drain

    def test_journal_and_drain_are_distinct_from_finished_adapter_operation(self):
        self.assertNotEqual(n.STATE, n.a.STATE)
        self.assertNotEqual(n.MARKER, n.a.MARKER)
        self.assertEqual(n.STATE.name, "native-rollout.json")

    def test_pending_missing_stale_or_wrong_scope_approval_cannot_promote(self):
        approval = {"operation": n.OP, "decision": "A", "scope": n.APPROVAL_SCOPE,
                    "source": "direct-human-user-reply", "approved_at": 1000, "expires_at": 1200}
        n.assert_approval(approval, now=1100)
        for key, value in (("decision", "pending"), ("scope", "images-only"), ("source", "assumption"),
                           ("operation", "0" * 32), ("approved_at", 1101), ("expires_at", 1050)):
            rejected = dict(approval); rejected[key] = value
            with self.subTest(key=key), self.assertRaises(n.h.DeploymentError): n.assert_approval(rejected, now=1100)
        with self.assertRaises(n.h.DeploymentError): n.assert_approval({}, now=1100)

    def test_absent_explicit_approval_blocks_before_any_docker_or_admission_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(n, "STATE", Path(directory) / "not-created.json"), patch.object(n, "private_json", side_effect=n.h.DeploymentError("no approval")), patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect") as inspect:
                with self.assertRaises(n.h.DeploymentError): n.run_locked()
                docker.assert_not_called(); inspect.assert_not_called()

    def test_text_image_only_gate_is_not_accepted_as_native_wide_admission(self):
        manifest = manifest_fixture(); manifest["admission_coverage"] = "text-image-only"
        with self.assertRaises(n.h.DeploymentError): n.validate_manifest(manifest, native_fixture())

    def test_gate_probe_inventory_includes_non_image_native_paid_routes_and_video_creates(self):
        probes = set(n.GATE_PROBES)
        required = {("GET", "/v1/realtime"), ("POST", "/v1/audio/speech"),
                    ("POST", "/v1/embeddings"), ("POST", "/v1/models/issue190-probe:generateContent"),
                    ("POST", "/v1beta/models/issue190-probe:generateContent"),
                    ("POST", "/mj/submit/imagine"), ("POST", "/suno/submit/music"),
                    ("POST", "/v1/videos"), ("POST", "/v1/video-jobs")}
        self.assertTrue(required <= probes)

    def test_only_frozen_native_candidate_and_exact_price_source_are_accepted(self):
        native = native_fixture()
        n.validate_manifest(manifest_fixture(), native)
        for key, bad in (("candidate_image", "sha256:" + "f" * 64),
                         ("pricing_source_sha256", "0" * 64),
                         ("before_id", "e" * 64), ("policy_sha256", "1" * 64)):
            manifest = manifest_fixture(); manifest[key] = bad
            with self.subTest(key=key), self.assertRaises(n.h.DeploymentError):
                n.validate_manifest(manifest, native)

    def test_ui_probes_cannot_be_generation_paths_redirects_or_empty_evidence(self):
        for assets in ({}, {"/v1/images/generations": "d" * 64},
                       {"https://outside.invalid/": "d" * 64},
                       {"/": "d" * 64, "/assets/../secret": "e" * 64}):
            manifest = manifest_fixture(); manifest["public_assets"] = assets
            with self.subTest(assets=assets), self.assertRaises(n.h.DeploymentError):
                n.validate_manifest(manifest, native_fixture())

    def test_actual_frozen_static_js_and_css_paths_are_validated_without_broadening_to_api(self):
        manifest = manifest_fixture()
        manifest["public_assets"] = {"/": "d" * 64,
            "/static/js/vendor-ui-primitives.36932e36fe.js": "e" * 64,
            "/static/js/index.a935d36b34.js": "f" * 64,
            "/static/css/index.0c72cb2ccf.css": "a" * 64}
        n.validate_manifest(manifest, native_fixture())

    def test_financial_mode_requires_both_environment_and_serving_status(self):
        native = native_fixture()
        status = {"success": True, "data": {"quota_db_authoritative": True, "enable_batch_update": False}}
        n.assert_financial_mode(native, status)
        for mutation in (lambda: native["Config"]["Env"].append("QUOTA_DB_AUTHORITATIVE=false"),
                         lambda: status["data"].update(enable_batch_update=True),
                         lambda: status["data"].update(quota_db_authoritative=False)):
            native = native_fixture(); status = {"success": True, "data": {"quota_db_authoritative": True, "enable_batch_update": False}}
            mutation()
            with self.assertRaises(n.h.DeploymentError): n.assert_financial_mode(native, status)

    def test_quiet_evidence_rejects_unknown_or_busy_native_and_unsettled_video(self):
        good = {"image_jobs_active": 0, "native_tcp_connections": 0,
                "native_reading": 0, "native_writing": 1, "public_video_unsettled": 0,
                "native_tasks_unsettled": 0, "video_jobs_unsettled": {key: 0 for key in n.VIDEO_STORES},
                "admission_verified": True, "internal_submitters_excluded": True}
        n.assert_idle_sample(good)
        for key, value in (("native_tcp_connections", 1), ("public_video_unsettled", 1),
                           ("native_writing", 2), ("admission_verified", None),
                           ("video_jobs_unsettled", {key: None for key in n.VIDEO_STORES})):
            sample = copy.deepcopy(good); sample[key] = value
            with self.subTest(key=key), self.assertRaises(n.h.DeploymentError): n.assert_idle_sample(sample)

    def test_current_video_instances_cannot_be_replaced_or_reconfigured(self):
        before = {name: dict(native_fixture(), Name="/" + name) for name in n.VIDEO_CONTAINERS}
        with patch.object(n.a, "inspect", side_effect=lambda name: copy.deepcopy(before[name])):
            n.assert_video_unchanged(before)
        changed = copy.deepcopy(before); changed[next(iter(changed))]["Config"]["Env"].append("ALTERED=true")
        with patch.object(n.a, "inspect", side_effect=lambda name: changed[name]), self.assertRaises(n.h.DeploymentError):
            n.assert_video_unchanged(before)

    def test_stop_is_sigterm_only_never_docker_stop_or_sigkill(self):
        original = native_fixture()
        stopped = copy.deepcopy(original); stopped["State"].update(Running=False, Restarting=False, Status="exited", Pid=0)
        with patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect", return_value=stopped), patch.object(n, "save"):
            n.terminate_original({"before": original, "phase": "drained"})
        self.assertEqual(docker.call_count, 1)
        self.assertEqual(docker.call_args.args, ("/containers/" + original["Id"] + "/kill?signal=SIGTERM", "POST"))

    def test_signal_outcome_unknown_is_observed_not_sent_again(self):
        original = native_fixture()
        stopped = copy.deepcopy(original); stopped["State"].update(Running=False, Restarting=False, Status="exited", Pid=0)
        with patch.object(n.a, "docker", side_effect=TimeoutError) as docker, patch.object(n.a, "inspect", return_value=stopped), patch.object(n, "save"):
            n.terminate_original({"before": original})
        self.assertEqual(docker.call_count, 1)

    def test_running_or_drifted_original_never_gets_force_killed_after_timeout(self):
        original = native_fixture()
        with patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect", return_value=original), patch.object(n, "save"), patch.object(n.time, "monotonic", side_effect=[0, 1, 151]), patch.object(n.time, "sleep"):
            with self.assertRaises(n.h.DeploymentError): n.terminate_original({"before": original})
        self.assertEqual(docker.call_count, 1)

    def test_original_exit_requires_two_consecutive_complete_stopped_observations(self):
        original = native_fixture()
        stopped = copy.deepcopy(original)
        stopped["State"].update(Running=False, Restarting=False, Status="exited", Pid=0)
        with patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect", return_value=stopped) as inspect, patch.object(n, "save"), patch.object(n.time, "sleep"):
            state = {"before": original}
            n.terminate_original(state)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(docker.call_count, 1)
        self.assertTrue(state["original_stopped"])

    def test_bare_running_false_or_restarting_dead_state_never_counts_as_stopped(self):
        original = native_fixture()
        for state_bits in ({"Running": False},
                {"Running": False, "Restarting": True, "Status": "restarting", "Pid": 0},
                {"Running": False, "Restarting": False, "Status": "dead", "Pid": 0},
                {"Running": False, "Restarting": False, "Status": "exited", "Pid": 111}):
            stopped = copy.deepcopy(original); stopped["State"] = state_bits
            with self.subTest(state=state_bits), patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect", return_value=stopped), patch.object(n, "save"), self.assertRaises(n.h.DeploymentError):
                n.terminate_original({"before": original})
            self.assertEqual(docker.call_count, 1)

    def test_post_term_restart_count_host_config_or_mount_drift_blocks_stop_evidence(self):
        original = native_fixture()
        mutations = (lambda item: item.update(RestartCount=1),
                     lambda item: item["HostConfig"].update(RestartPolicy={"Name": "always"}),
                     lambda item: item["Mounts"][0].update(RW=False))
        for mutation in mutations:
            observed = copy.deepcopy(original)
            observed["State"].update(Running=False, Restarting=False, Status="exited", Pid=0)
            mutation(observed)
            with self.subTest(mutation=mutation), patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect", return_value=observed), patch.object(n, "save"), self.assertRaises(n.h.DeploymentError):
                n.terminate_original({"before": original})
            self.assertEqual(docker.call_count, 1)

    def test_process_running_again_after_first_exit_observation_preserves_maintenance(self):
        original = native_fixture(); stopped = copy.deepcopy(original)
        stopped["State"].update(Running=False, Restarting=False, Status="exited", Pid=0)
        with patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect", side_effect=[stopped, original]), patch.object(n, "save"), patch.object(n.time, "sleep"), self.assertRaises(n.h.DeploymentError):
            n.terminate_original({"before": original})
        self.assertEqual(docker.call_count, 1)

    def test_existing_original_term_intent_does_not_send_second_signal(self):
        with patch.object(n.a, "docker") as docker, patch.object(n.a, "inspect") as inspect, patch.object(n, "save"), self.assertRaises(n.h.DeploymentError):
            n.terminate_original({"before": native_fixture(), "terminate_intent": True})
        docker.assert_not_called(); inspect.assert_not_called()

    def candidate_fixture(self, *, created=False):
        original = native_fixture(); manifest = manifest_fixture()
        payload = n.h.clone_create_config(original, n.CANDIDATE_IMAGE, n.OP,
            image_labels={"com.aixingtuyun.image-fixes-policy-sha256": n.POLICY_SHA256})
        candidate = copy.deepcopy(original)
        candidate.update(Id="d" * 64, Image=n.CANDIDATE_IMAGE,
            Config={key: value for key, value in payload.items() if key not in {"HostConfig", "NetworkingConfig"}})
        if created:
            candidate["State"].update(Running=False, Restarting=False, Status="created", Pid=0,
                StartedAt="0001-01-01T00:00:00Z")
        return original, manifest, candidate

    def test_never_started_created_candidate_is_retained_without_signal_or_start(self):
        original, manifest, candidate = self.candidate_fixture(created=True)
        with patch.object(n.a, "inspect", return_value=candidate), patch.object(n.a, "docker") as docker, patch.object(n, "assert_owned_admission"), patch.object(n.time, "sleep"), patch.object(n, "save"):
            n.retain_failed_candidate({"before": original, "new_id": candidate["Id"]}, manifest)
        paths = [call.args[0] for call in docker.call_args_list]
        self.assertTrue(any("/rename?name=" in path for path in paths))
        self.assertFalse(any("/kill" in path or path.endswith("/start") for path in paths))

    def test_created_candidate_with_start_or_restart_evidence_cannot_be_retained_as_inert(self):
        mutations = (lambda item: item.update(RestartCount=1),
                     lambda item: item["State"].update(StartedAt="2026-10-10T02:00:00Z"),
                     lambda item: item["State"].update(Restarting=True),
                     lambda item: item["HostConfig"].update(RestartPolicy={"Name": "always"}))
        for mutation in mutations:
            original, manifest, candidate = self.candidate_fixture(created=True); mutation(candidate)
            with self.subTest(mutation=mutation), patch.object(n.a, "inspect", return_value=candidate), patch.object(n.a, "docker") as docker, patch.object(n, "assert_owned_admission"), self.assertRaises(n.h.DeploymentError):
                n.retain_failed_candidate({"before": original, "new_id": candidate["Id"]}, manifest)
            docker.assert_not_called()

    def test_existing_rollback_term_intent_does_not_resignal_running_candidate(self):
        original, manifest, candidate = self.candidate_fixture()
        with patch.object(n.a, "inspect", return_value=candidate), patch.object(n.a, "docker") as docker, patch.object(n, "assert_replacement_idle"), patch.object(n, "save"), patch.object(n.time, "sleep"), patch.object(n.time, "monotonic", side_effect=[0, 1, 151]), self.assertRaises(n.h.DeploymentError):
            n.retain_failed_candidate({"before": original, "new_id": candidate["Id"], "rollback_term_intent": True}, manifest)
        docker.assert_not_called()

    def test_unknown_original_stop_during_recovery_does_not_restart_or_release(self):
        original = native_fixture(); observed = copy.deepcopy(original); observed["State"] = {"Running": False}
        with patch.object(n.a, "inspect", return_value=observed), patch.object(n.a, "docker") as docker, patch.object(n, "save"), patch.object(n, "await_native"), self.assertRaises(n.h.DeploymentError):
            n.restore_original({"before": original, "terminate_intent": True}, manifest_fixture())
        docker.assert_not_called()

    def test_unknown_create_only_reconciles_exact_owned_instance(self):
        manifest = manifest_fixture()
        candidate = dict(native_fixture(), Id="d" * 64, Image=n.CANDIDATE_IMAGE)
        candidate["Config"]["Labels"] = {n.h.OP_LABEL: n.OP,
                                           "com.aixingtuyun.image-fixes-policy-sha256": n.POLICY_SHA256}
        state = {"before": native_fixture(), "new_id": None}
        with patch.object(n.a, "inspect_optional", return_value=candidate), patch.object(n, "save"):
            self.assertEqual(n.reconcile_create(state, manifest), candidate["Id"])
        candidate["Config"]["Labels"][n.h.OP_LABEL] = "foreign"
        with patch.object(n.a, "inspect_optional", return_value=candidate), self.assertRaises(n.h.DeploymentError):
            n.reconcile_create(state, manifest)

    def test_inert_version_payload_has_no_live_credentials_mounts_aliases_or_ports(self):
        payload = n.version_payload(manifest_fixture())
        self.assertEqual(payload["Entrypoint"], ["/new-api"])
        self.assertEqual(payload["Cmd"], ["--version"])
        self.assertEqual(payload["Env"], [])
        self.assertNotIn("Volumes", payload)
        self.assertNotIn("NetworkingConfig", payload)
        self.assertEqual(payload["HostConfig"]["NetworkMode"], "none")
        self.assertEqual(payload["HostConfig"]["Binds"], [])
        self.assertTrue(payload["HostConfig"]["ReadonlyRootfs"])
        self.assertEqual(payload["HostConfig"]["RestartPolicy"], {"Name": "no"})

    def test_existing_or_interrupted_native_journal_blocks_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "native-rollout.json"; state.write_text("{}")
            with patch.object(n, "STATE", state), patch.object(n.a, "docker") as docker:
                with self.assertRaises(n.h.DeploymentError): n.run_locked()
                docker.assert_not_called()

    def test_busy_rollback_never_stops_or_renames_replacement(self):
        state = {"new_id": "d" * 64, "before": native_fixture()}
        with patch.object(n, "assert_replacement_idle", side_effect=n.h.DeploymentError("unknown activity")), patch.object(n.a, "docker") as docker:
            with self.assertRaises(n.h.DeploymentError): n.retain_failed_candidate(state, manifest_fixture())
            docker.assert_not_called()

    def test_still_running_after_sigterm_is_not_restarted_or_released(self):
        state = {"before": native_fixture(), "terminate_intent": True, "original_stopped": False}
        with patch.object(n.a, "inspect", return_value=state["before"]), patch.object(n.a, "docker") as docker, patch.object(n.time, "monotonic", side_effect=[0, 1, 151]), patch.object(n.time, "sleep"):
            with self.assertRaises(n.h.DeploymentError): n.restore_original(state, manifest_fixture())
            docker.assert_not_called()

    def test_gate_syntax_reload_failure_before_stop_can_restore_exact_owned_admission(self):
        original = b"original"; gated = b"owned changed config"
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / "conf"; conf.write_bytes(gated)
            drain = Path(directory) / "DRAIN"; drain.write_bytes(n.MARKER)
            with patch.object(n, "CONF", conf), patch.object(n, "DRAIN", drain), patch.object(n, "assert_video_unchanged"), patch.object(n, "assert_owned_admission") as gate, patch.object(n.a, "write_conf") as write:
                n.release_admission({"video_before": {}}, original, gated, native_mutated=False)
                write.assert_called_once_with(original, gated)
                gate.assert_not_called()
                self.assertFalse(drain.exists())

    def test_external_configuration_change_retains_drain_and_does_not_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / "conf"; conf.write_bytes(b"foreign edit")
            drain = Path(directory) / "DRAIN"; drain.write_bytes(n.MARKER)
            with patch.object(n, "CONF", conf), patch.object(n, "DRAIN", drain), patch.object(n.a, "write_conf") as write:
                with self.assertRaises(n.h.DeploymentError):
                    n.release_admission({"video_before": {}}, b"original", b"gated", native_mutated=False)
                write.assert_not_called(); self.assertTrue(drain.exists())

    def test_clone_retains_env_uid_security_mounts_and_network_aliases(self):
        original = native_fixture(); before = copy.deepcopy(original)
        payload = n.h.clone_create_config(original, n.CANDIDATE_IMAGE, n.OP,
                    image_labels={"com.aixingtuyun.image-fixes-policy-sha256": n.POLICY_SHA256})
        self.assertEqual(original, before)
        self.assertEqual(payload["Env"], before["Config"]["Env"])
        self.assertEqual(payload["HostConfig"], before["HostConfig"])
        self.assertEqual(payload["User"], before["Config"]["User"])
        self.assertEqual(payload["NetworkingConfig"]["EndpointsConfig"]["app-net"]["Aliases"], ["new-api"])

    def test_failed_create_restores_same_original_and_current_storage_without_second_create(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory, create_failure="absent")
            with stack, self.assertRaises(TimeoutError): n.run_locked()
            self.assertEqual(sum(path.startswith("/containers/create") for path, _, _ in operations), 1)
            original = containers["a" * 64]
            self.assertEqual(original["Name"], "/" + n.h.NATIVE)
            self.assertTrue(original["State"]["Running"])
            self.assertEqual(original["Mounts"], native_fixture()["Mounts"])
            self.assertEqual(journal[-1]["phase"], "rolled_back")
            self.assertFalse(drain.exists())

    def test_lost_create_response_is_reconciled_retained_and_never_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory, create_failure="lost")
            with stack, self.assertRaises(TimeoutError): n.run_locked()
            self.assertEqual(sum(path.startswith("/containers/create") for path, _, _ in operations), 1)
            self.assertEqual(containers["d" * 64]["Name"], "/" + n.FAILED_NAME)
            self.assertFalse(containers["d" * 64]["State"]["Running"])
            self.assertEqual(containers["a" * 64]["Name"], "/" + n.h.NATIVE)
            self.assertTrue(containers["a" * 64]["State"]["Running"])
            self.assertTrue(any(row.get("create_reconciled") for row in journal))
            self.assertEqual(journal[-1]["phase"], "rolled_back")

    def test_partial_network_disconnect_reconnects_original_id_without_restoring_any_store(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory, disconnect_failure=True)
            with stack, self.assertRaises(TimeoutError): n.run_locked()
            self.assertEqual(sum(path.startswith("/containers/create") for path, _, _ in operations), 0)
            reconnects = [body for path, _, body in operations if path.endswith("/connect")]
            self.assertEqual([body["Container"] for body in reconnects], ["a" * 64])
            self.assertTrue(containers["a" * 64]["State"]["Running"])
            self.assertEqual(set(containers["a" * 64]["NetworkSettings"]["Networks"]), {"app-net", "internal"})
            self.assertEqual(journal[-1]["phase"], "rolled_back")

    def test_post_commit_reopen_failure_keeps_verified_new_native_without_rollback_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, containers, operations, journal, conf, drain = self.release_fixture(directory, reopen_failure=True)
            with stack, self.assertRaises(n.h.DeploymentError): n.run_locked()
            self.assertEqual(journal[-1]["phase"], "post_commit_recovery_required")
            self.assertTrue(containers["d" * 64]["State"]["Running"])
            self.assertFalse(containers["a" * 64]["State"]["Running"])
            self.assertEqual(containers["d" * 64]["Name"], "/" + n.h.NATIVE)
            self.assertTrue(drain.exists())
            self.assertFalse(any(path == "/containers/" + "a" * 64 + "/start" for path, _, _ in operations))
            self.assertFalse(any(path == "/containers/" + "d" * 64 + "/kill?signal=SIGTERM" for path, _, _ in operations))


if __name__ == "__main__":
    unittest.main()
