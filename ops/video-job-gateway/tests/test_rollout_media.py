"""Fresh release plans are tested without Docker, HTTP, SQL, or host mutations."""

import copy
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

SOURCE = pathlib.Path(__file__).resolve().parents[3] / "specs/186-nody-multimodal/rollout_media.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("rollout_media_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def manifest():
    op = "a" * 32
    targets = ("xtai-video-public-execution", "xtai-video-job-gateway-v2-production", "xtai-public-video-catalog176")
    return {"schema_version": "xtai-nody-media-rollout-v1", "operation_id": op,
            "private_root": "/opt/ai-api-stack/backups/nody-media-rollout-" + op,
            "candidate_images": {name: "sha256:" + str(index + 1) * 64 for index, name in enumerate(targets)},
            "candidate_sources": {name: str(index + 4) * 64 for index, name in enumerate(targets)},
            "image_profile": {"host_path": "/opt/xtai/secrets/video-billing/nody-image-input-186.json",
                              "container_path": "/run/secrets/video-billing/nody-image-input-186.json", "sha256": "7" * 64},
            "media_profile": {"host_path": "/opt/xtai/secrets/video-billing/nody-media-input-new.json",
                              "container_path": "/run/secrets/video-billing/nody-media-input-new.json", "sha256": "8" * 64},
            "expected_media_prices": [{"model": "wan3.0-video", "operation_mode": "all_reference", "resolution": "480p", "duration": 2,
                                       "image_count": 1, "video_count": 1, "audio_count": 1, "generate_audio": True, "aspect_ratio": "16:9",
                                       "input_video_seconds_exact": "2.000000", "input_audio_seconds_exact": "2.040000", "amount_cny_exact": "1.800000"}],
            "gateway_env": {"VIDEO_JOB_NODYHUB_MEDIA_CONTRACT_FILE": "/run/secrets/video-billing/nody-media-input-new.json"}}


def snapshot(helper):
    rows = [{"id": model, "available": True, "operation_modes": ["text"], "resolutions": ["480p"],
             "aspect_ratios": ["16:9"], "durations": [2], "max_images": 0, "max_videos": 0} for model in helper.NODY_MODELS]
    rows.append({"id": "untouched-provider", "available": True, "operation_modes": ["text"]})
    image = [{"model": model, "operation_mode": mode, "image_count": count, "resolution": resolution, "duration": duration, "amount_cny_exact": "0.675000"}
             for model, (resolution, duration) in helper.LEGACY_IMAGES.items() for mode, count in (("reference", 1), ("all_reference", 2))]
    return {"/v1/capabilities": {"capabilities": {"video": {"models": rows}}},
            "/v1/video-prices": {"pricing": {"models": [{"model": model, "amount_cny_exact": "1.000000"} for model in helper.NODY_MODELS]},
                                  "image_reference_pricing": {"models": image}, "media_reference_pricing": {"models": []}},
            "/v1/models": {"data": [{"id": model} for model in helper.NODY_MODELS]},
            "/api/pricing": {"data": [{"model_name": model, "price": 1, "enable_groups": ["video", "auto"]} for model in helper.NODY_MODELS]}}


def expanded(helper, before, expected):
    result = copy.deepcopy(before)
    result["/v1/video-prices"]["media_reference_pricing"]["models"] = copy.deepcopy(expected)
    for row in result["/v1/capabilities"]["capabilities"]["video"]["models"]:
        if row["id"] not in helper.NODY_MODELS:
            continue
        prices = [price for price in expected if price["model"] == row["id"]]
        row["media_reference"] = {"supported": bool(prices), "available": bool(prices), "specifications": prices}
        for field, count in (("reference_video", "video_count"), ("reference_audio", "audio_count"), ("reference_video_audio", "video_count")):
            matches = [price for price in prices if price[count] and (field != "reference_video_audio" or price["audio_count"])]
            row[field] = {"supported": bool(matches), "available": bool(matches), "specifications": matches}
        if prices:
            row["operation_modes"].append("all_reference")
            row["max_images"] = row["max_videos"] = row["max_audios"] = 1
    return result


class FakeRuntime:
    def __init__(self, helper, value):
        self.helper = helper
        self.manifest = value
        self.files = {}
        self.events = []
        self.rows = []
        self.native = {"Id": "fresh-native", "Image": "fresh-native-image", "State": {"Running": True}}
        self.containers = {}
        self.candidates = {}
        self.observed = snapshot(helper)
        for name in helper.TARGETS:
            config = {"Image": "old-image", "Env": ["VIDEO_JOB_NODYHUB_IMAGE_CONTRACT_FILE=" + value["image_profile"]["container_path"],
                     "VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_HOSTS=upload.aixingtuyun.com", "PRIVATE_SECRET=preserve"], "Labels": {"original": "yes"}}
            if name == helper.PUBLIC:
                config["Entrypoint"] = ["/usr/local/bin/public-video", "--log-dir", "/tmp/public-video-logs"]
            mounts = [{"Destination": "/data", "Source": helper.DATA.get(name, ""), "RW": True},
                      {"Destination": "/run/secrets/video-billing", "Source": helper.SECRETS, "RW": False}]
            self.containers[name] = {"Id": "before-" + name, "Name": "/" + name, "Image": "old-image", "State": {"Running": True},
                                     "Config": config, "HostConfig": {"Binds": ["retain"]}, "Mounts": mounts,
                                     "NetworkSettings": {"Networks": {"app-net": {"Aliases": [name, "custom"], "IPAddress": "172.18.0.5"}}}}
            self.candidates[name] = {"Id": value["candidate_images"][name], "Config": {"Labels": {helper.SOURCE_LABEL: value["candidate_sources"][name]}}}

    def exists(self, filename):
        return filename in self.files

    def write(self, filename, value, *, exclusive=False):
        if exclusive and self.exists(filename):
            raise self.helper.RolloutError("owned output already exists")
        self.files[filename] = copy.deepcopy(value)

    def read(self, filename):
        return copy.deepcopy(self.files[filename])

    def inspect(self, name):
        if name == self.helper.NATIVE:
            return copy.deepcopy(self.native)
        for value in self.containers.values():
            if name in (value["Id"], value["Name"].lstrip("/")):
                return copy.deepcopy(value)
        raise self.helper.RolloutError("container missing")

    def image(self, name):
        return copy.deepcopy(next(row for row in self.candidates.values() if row["Id"] == name))

    def names(self):
        return [row["Name"].lstrip("/") for row in self.containers.values()]

    def profile_digest(self, path):
        return next(row["sha256"] for row in (self.manifest["image_profile"], self.manifest["media_profile"]) if row["host_path"] == path)

    def snapshot(self, name):
        if self.inspect(name)["Image"] == self.manifest["candidate_images"][name]:
            return expanded(self.helper, self.observed, self.manifest["expected_media_prices"])
        return copy.deepcopy(self.observed)

    def backup(self, name, label):
        self.events.append(("backup", name, label))

    def postgres_backup(self):
        self.events.append(("pg_backup",))

    def inflight(self, *, rollback=False):
        self.helper.assert_inflight(self.rows, rollback=rollback)
        self.events.append(("inflight", rollback))

    def drain(self, name, operation, *, remove=False):
        self.events.append(("drain", name, remove))

    def stop(self, identity):
        self.events.append(("stop", identity))
        next(row for row in self.containers.values() if row["Id"] == identity)["State"]["Running"] = False

    def rename(self, identity, name):
        self.events.append(("rename", identity, name))
        next(row for row in self.containers.values() if row["Id"] == identity)["Name"] = "/" + name

    def disconnect(self, info):
        next(row for row in self.containers.values() if row["Id"] == info["Id"])["NetworkSettings"]["Networks"] = {}

    def create(self, name, config):
        identity = "new-" + name
        self.events.append(("create", name, copy.deepcopy(config)))
        self.containers[identity] = {"Id": identity, "Name": "/" + name, "Image": config["Image"], "State": {"Running": False},
                                     "Config": copy.deepcopy(config), "HostConfig": copy.deepcopy(config["HostConfig"]),
                                     "NetworkSettings": {"Networks": copy.deepcopy(config["NetworkingConfig"]["EndpointsConfig"])}}
        return identity

    def start(self, identity):
        self.events.append(("start", identity))
        next(row for row in self.containers.values() if row["Id"] == identity)["State"]["Running"] = True

    def connect(self, identity, endpoints):
        next(row for row in self.containers.values() if row["Id"] == identity)["NetworkSettings"]["Networks"].update(copy.deepcopy(endpoints))

    def wait_health(self, name):
        self.helper.require(self.inspect(name)["State"]["Running"], "unhealthy fake")

    def reload_routes(self):
        self.events.append(("reload",))


class RolloutMediaTests(unittest.TestCase):
    def test_explicit_approved_operator_policy_is_separate_and_digest_bound(self):
        value = manifest()
        value.update(allow_untested=True,
                     operator_testing_profile={"host_path": "/opt/xtai/secrets/video-billing/nody-operator-testing-new.json",
                         "container_path": "/run/secrets/video-billing/nody-operator-testing-new.json", "sha256": "9" * 64},
                     expected_operator_rules=[{"model": "wan3.0-video", "verification_status": "unverified",
                         "pricing_kind": "estimated_reservation", "admission_mode": "operator_testing", "is_upper_bound": False}])
        value["gateway_env"]["VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE"] = value["operator_testing_profile"]["container_path"]
        checked = self.helper.validate_manifest(value)
        runtime = FakeRuntime(self.helper, checked)
        before = runtime.inspect(self.helper.GATEWAYS[0])
        config = self.helper.create_config(before, checked, self.helper.GATEWAYS[0])
        self.assertEqual(self.helper.environment({"Config": config})["VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE"], value["operator_testing_profile"]["container_path"])
        for change in ({"operator_testing_profile": None}, {"expected_operator_rules": []},
                       {"expected_operator_rules": [{"model": "wan3.0-video", "verification_status": "verified"}]}):
            with self.subTest(change=change), self.assertRaises(self.helper.RolloutError):
                self.helper.validate_manifest({**value, **change})

    def test_operator_text_or_private_estimate_envelope_blocks_old_code_rollback(self):
        for payload in ({"model": "wan3.0-video", "mode": "text", "_nody_operator_testing": True},
                        {"model": "wan3.0-video", "mode": "text", "_public_reservation": {"schema_version": "xtai-public-video-estimated-reservation-v1"}}):
            with self.subTest(payload=payload), self.assertRaises(self.helper.RolloutError):
                self.helper.assert_inflight([{"status": "running", "upstream_task_id": "original", "provider_id": "nodyhub", "payload": payload}], rollback=True)

    def test_approved_rule_projection_is_unverified_and_does_not_fabricate_exact_profiles(self):
        value = manifest()
        before = snapshot(self.helper)
        after = expanded(self.helper, before, value["expected_media_prices"])
        rules = [{"model": "wan3.0-video-prime", "max_videos": 5, "max_audios": 5, "verification_status": "unverified",
                  "pricing_kind": "estimated_reservation", "admission_mode": "operator_testing", "is_upper_bound": False}]
        after["/v1/video-prices"]["operator_testing"] = {"enabled": True, "verification_status": "unverified", "admission_mode": "operator_testing", "rules": rules}
        prime = next(row for row in after["/v1/capabilities"]["capabilities"]["video"]["models"] if row["id"] == "wan3.0-video-prime")
        prime["operator_testing"] = {"supported": True, "available": True, "verification_status": "unverified", "admission_mode": "operator_testing", "rules": rules}
        prime["media_reference"].update(supported=True, available=True, verification_status="unverified", admission_mode="operator_testing", rules=rules)
        for kind in ("reference_video", "reference_audio", "reference_video_audio"):
            prime[kind].update(supported=True, available=True, verification_status="unverified", admission_mode="operator_testing", rules=rules)
        self.helper.compare_snapshot(before, after, value["expected_media_prices"], operator_rules=rules)
        corrupt = copy.deepcopy(after)
        corrupt["/v1/video-prices"]["operator_testing"]["verification_status"] = "verified"
        with self.assertRaises(self.helper.RolloutError):
            self.helper.compare_snapshot(before, corrupt, value["expected_media_prices"], operator_rules=rules)

    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_windows_import_performs_no_external_or_host_io(self):
        with patch("subprocess.run") as run, patch("subprocess.Popen") as spawn:
            load_helper()
        run.assert_not_called()
        spawn.assert_not_called()

    def test_manifest_is_fresh_fixed_scope_and_does_not_choose_a_billing_policy(self):
        value = manifest()
        self.assertEqual(self.helper.validate_manifest(value), value)
        for change in ({"private_root": "/opt/ai-api-stack/backups/nody-multimodal-186-20261007/rollout"},
                       {"operation_id": "old186"}, {"candidate_images": {**value["candidate_images"], "ai-api-stack-new-api-1": "sha256:" + "a" * 64}},
                       {"gateway_env": {"VIDEO_JOB_NODYHUB_TESTING_RESERVE": "999"}},
                       {"allow_untested": True},
                       {"image_profile": {**value["image_profile"], "host_path": "/tmp/replace-old.json"}},
                       {"media_profile": {**value["media_profile"], "sha256": "bad"}}):
            with self.subTest(change=change), self.assertRaises(self.helper.RolloutError):
                self.helper.validate_manifest({**value, **change})

    def test_config_preserves_data_secrets_image_contract_and_all_network_settings(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        original = runtime.inspect(self.helper.GATEWAYS[0])
        before = copy.deepcopy(original)
        config = self.helper.create_config(original, value, self.helper.GATEWAYS[0])
        env = dict(item.split("=", 1) for item in config["Env"])
        self.assertEqual(env["VIDEO_JOB_NODYHUB_IMAGE_CONTRACT_FILE"], value["image_profile"]["container_path"])
        self.assertEqual(env["PRIVATE_SECRET"], "preserve")
        self.assertEqual(env["VIDEO_JOB_NODYHUB_MEDIA_CONTRACT_FILE"], value["media_profile"]["container_path"])
        self.assertEqual(config["Image"], value["candidate_images"][self.helper.GATEWAYS[0]])
        self.assertEqual(config["HostConfig"], original["HostConfig"])
        self.assertEqual(config["NetworkingConfig"]["EndpointsConfig"]["app-net"]["Aliases"], [self.helper.GATEWAYS[0], "custom"])
        self.assertEqual(original, before)

    def test_catalog_additions_are_exact_and_retain_text_six_images_and_other_providers(self):
        value = manifest()
        before = snapshot(self.helper)
        after = expanded(self.helper, before, value["expected_media_prices"])
        self.helper.compare_snapshot(before, after, value["expected_media_prices"])
        alterations = []
        wrong_price = copy.deepcopy(after); wrong_price["/v1/video-prices"]["pricing"]["models"][0]["amount_cny_exact"] = "2.000000"; alterations.append(wrong_price)
        image_removed = copy.deepcopy(after); image_removed["/v1/video-prices"]["image_reference_pricing"]["models"].pop(); alterations.append(image_removed)
        extra_profile = copy.deepcopy(after); extra_profile["/v1/video-prices"]["media_reference_pricing"]["models"].append({"model": "omni-flash"}); alterations.append(extra_profile)
        unrelated = copy.deepcopy(after); unrelated["/v1/capabilities"]["capabilities"]["video"]["models"][-1]["operation_modes"].append("reference"); alterations.append(unrelated)
        missing_field = copy.deepcopy(after); del missing_field["/v1/capabilities"]["capabilities"]["video"]["models"][1]["media_reference"]; alterations.append(missing_field)
        for changed in alterations:
            with self.subTest(changed=changed), self.assertRaises(self.helper.RolloutError):
                self.helper.compare_snapshot(before, changed, value["expected_media_prices"])

    def test_promote_accepts_only_metadata_order_version_placement_and_authorized_suffix(self):
        value = manifest()
        before = snapshot(self.helper)
        before["/api/pricing"]["data"][0].update(description="Original text.", pricing_version="same-metadata-hash")
        after = expanded(self.helper, before, value["expected_media_prices"])
        del after["/api/pricing"]["data"][0]["pricing_version"]
        after["/api/pricing"]["data"][1]["pricing_version"] = "same-metadata-hash"
        after["/api/pricing"]["data"][0]["description"] += "另支持已验证媒体模式；see exact profiles."
        after["/api/pricing"]["data"].reverse()
        after["/v1/video-prices"]["pricing"]["models"].reverse()
        after["/v1/capabilities"]["capabilities"]["video"]["models"].reverse()
        self.helper.compare_snapshot(before, after, value["expected_media_prices"])
        changed = copy.deepcopy(after)
        changed["/v1/video-prices"]["pricing"]["models"][0]["amount_cny_exact"] = "2.000000"
        with self.assertRaises(self.helper.RolloutError):
            self.helper.compare_snapshot(before, changed, value["expected_media_prices"])

    def test_rollback_is_semantic_but_cannot_keep_expanded_description_or_changed_money(self):
        before = snapshot(self.helper)
        before["/api/pricing"]["data"][0].update(description="Original text.", pricing_version="same-metadata-hash")
        after = copy.deepcopy(before)
        del after["/api/pricing"]["data"][0]["pricing_version"]
        after["/api/pricing"]["data"][1]["pricing_version"] = "same-metadata-hash"
        after["/api/pricing"]["data"].reverse()
        after["/v1/models"]["data"].reverse()
        after["/v1/video-prices"]["pricing"]["models"].reverse()
        after["/v1/capabilities"]["capabilities"]["video"]["models"].reverse()
        self.helper.compare_snapshot(before, after, [], rollback=True)
        for field, value in (("price", 2), ("description", "Original text.另支持已验证媒体模式；unrestored"), ("pricing_revision", "changed")):
            altered = copy.deepcopy(after)
            row = next(row for row in altered["/api/pricing"]["data"] if row["model_name"] == self.helper.NODY_MODELS[0])
            row[field] = value
            with self.subTest(field=field), self.assertRaises(self.helper.RolloutError):
                self.helper.compare_snapshot(before, altered, [], rollback=True)

    def test_unrelated_ordered_frame_sequence_drift_is_not_normalized(self):
        value = manifest()
        before = snapshot(self.helper)
        before["/v1/capabilities"]["capabilities"]["video"]["models"][-1]["ordered_frames"] = ["first", "last"]
        after = expanded(self.helper, before, value["expected_media_prices"])
        after["/v1/capabilities"]["capabilities"]["video"]["models"][-1]["ordered_frames"].reverse()
        with self.assertRaises(self.helper.RolloutError):
            self.helper.compare_snapshot(before, after, value["expected_media_prices"])
        restored = copy.deepcopy(before)
        restored["/v1/capabilities"]["capabilities"]["video"]["models"][-1]["ordered_frames"].reverse()
        with self.assertRaises(self.helper.RolloutError):
            self.helper.compare_snapshot(before, restored, [], rollback=True)

    def test_active_new_modes_block_rollback_but_deployed_legacy_grok_jobs_do_not(self):
        self.helper.assert_inflight([{"status": "running", "upstream_task_id": "known", "provider_id": "nodyhub", "payload": {
            "model": "grok-video-3", "mode": "reference", "resolution": "720p", "duration": 6, "aspect_ratio": "16:9",
            "generate_audio": True, "images": [{}]}}], rollback=True)
        for row in ({"status": "queued", "upstream_task_id": "", "provider_id": "nodyhub", "payload": {}},
                    {"status": "running", "upstream_task_id": "", "provider_id": "nodyhub", "payload": {}},
                    {"status": "running", "upstream_task_id": "known", "provider_id": "nodyhub", "payload": {"_nody_media_contract": True}}):
            with self.subTest(row=row), self.assertRaises(self.helper.RolloutError):
                self.helper.assert_inflight([row], rollback=True)

    def test_stage_records_current_native_identity_fresh_backups_and_no_stop(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        rollout.stage()
        state = runtime.read("state.json")
        self.assertEqual(state["native"], {"id": "fresh-native", "image": "fresh-native-image"})
        self.assertEqual(state["phase"], "staged")
        self.assertEqual(sum(event[0] == "backup" for event in runtime.events), 2)
        self.assertIn(("pg_backup",), runtime.events)
        self.assertFalse(any(event[0] == "stop" for event in runtime.events))
        with self.assertRaises(self.helper.RolloutError):
            rollout.stage()

    def test_unsafe_queue_or_changed_native_prevents_every_stop(self):
        for change in ("queue", "native"):
            value = manifest()
            runtime = FakeRuntime(self.helper, value)
            rollout = self.helper.Rollout(value, runtime)
            rollout.stage()
            if change == "queue":
                runtime.rows = [{"status": "submitting", "upstream_task_id": "", "provider_id": "nodyhub", "payload": {}}]
            else:
                runtime.native["Id"] = "another-native"
            with self.subTest(change=change), self.assertRaises(self.helper.RolloutError):
                rollout.promote()
            self.assertFalse(any(event[0] == "stop" for event in runtime.events))

    def test_promote_and_rollback_preserve_current_data_and_never_stop_native(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        rollout.stage()
        rollout.promote()
        self.assertEqual(runtime.read("state.json")["phase"], "promoted_verified")
        created = [event for event in runtime.events if event[0] == "create"]
        self.assertEqual([event[1] for event in created], list(self.helper.TARGETS))
        self.assertTrue(all(event[2]["HostConfig"] == {"Binds": ["retain"]} for event in created))
        self.assertFalse(any(event[0] == "stop" and event[1] == "fresh-native" for event in runtime.events))
        rollout.rollback()
        self.assertEqual(runtime.read("state.json")["phase"], "rolled_back_verified")
        self.assertEqual(runtime.read("state.json")["drains"], [])
        self.assertTrue(all(runtime.inspect(name)["Id"] == "before-" + name for name in self.helper.TARGETS))
        self.assertEqual(sum(event[0] == "pg_backup" for event in runtime.events), 1)

    def test_new_active_job_blocks_rollback_before_any_stop(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        rollout.stage(); rollout.promote()
        before = len(runtime.events)
        runtime.rows = [{"status": "running", "upstream_task_id": "original-task", "provider_id": "nodyhub", "payload": {"_nody_media_contract": True}}]
        with self.assertRaises(self.helper.RolloutError):
            rollout.rollback()
        self.assertEqual(runtime.events[before:], [])

    def test_partial_public_rename_can_roll_back_without_a_current_public_name(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        rollout.stage()
        state = runtime.read("state.json")
        rollout.drains(state)
        state["swapped"] = [self.helper.PUBLIC]
        state["phase"] = "draining"
        rollout.save(state)
        old = runtime.inspect(self.helper.PUBLIC)
        runtime.stop(old["Id"])
        runtime.rename(old["Id"], self.helper.replacement_names(self.helper.PUBLIC, value["operation_id"])[0])
        runtime.disconnect(old)
        rollout.rollback()
        self.assertEqual(runtime.read("state.json")["phase"], "rolled_back_verified")

    def test_tampered_candidate_source_is_rejected_before_replacement(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        runtime.candidates[self.helper.GATEWAYS[0]]["Config"]["Labels"][self.helper.SOURCE_LABEL] = "wrong"
        with self.assertRaises(self.helper.RolloutError):
            self.helper.Rollout(value, runtime).stage()
        self.assertFalse(any(event[0] == "stop" for event in runtime.events))

    def test_detached_runner_is_bounded_and_uses_no_shell_or_remote_command(self):
        command = self.helper.runner_command("/private/manifest.json")
        self.assertEqual(command[:4], ["timeout", "--signal=TERM", "--kill-after=600s", "900s"])
        self.assertEqual(command[-4:], ["run", "--manifest", "/private/manifest.json", "--runner"])
        self.assertNotIn("ssh", command)

    def test_each_replacement_failure_attempts_one_owned_current_data_recovery(self):
        for boundary in ("stop", "rename", "disconnect", "create", "start", "wait_health", "reload_routes"):
            value = manifest()
            runtime = FakeRuntime(self.helper, value)
            rollout = self.helper.Rollout(value, runtime)
            original = getattr(runtime, boundary)
            fired = [False]

            def fail_once(*args, **kwargs):
                result = original(*args, **kwargs)
                if not fired[0]:
                    fired[0] = True
                    raise self.helper.RolloutError("injected replacement failure")
                return result

            with self.subTest(boundary=boundary), patch.object(runtime, boundary, side_effect=fail_once):
                with self.assertRaises(self.helper.RolloutError):
                    rollout.execute("run")
            self.assertEqual(runtime.read("state.json")["phase"], "rolled_back_verified")
            self.assertEqual(runtime.read("recovery.json")["attempts"], 1)
            self.assertTrue(runtime.exists("operation-error.json"))
            self.assertFalse(any(event[0] == "stop" and event[1] == "fresh-native" for event in runtime.events))

    def test_new_media_profile_corruption_does_not_prevent_baseline_recovery(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        rollout.stage(); rollout.promote()
        original = runtime.profile_digest

        def changed(path):
            return "0" * 64 if path == value["media_profile"]["host_path"] else original(path)

        with patch.object(runtime, "profile_digest", side_effect=changed):
            rollout.rollback()
        self.assertEqual(runtime.read("state.json")["phase"], "rolled_back_verified")

    def test_timeout_interruption_uses_recovery_without_replaying_generation_or_recovery(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        original = runtime.create
        fired = [False]

        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            if not fired[0]:
                fired[0] = True
                rollout.interrupt()
            return result

        with patch.object(runtime, "create", side_effect=interrupted):
            with self.assertRaises(self.helper.RolloutInterrupted):
                rollout.execute("run")
        self.assertEqual(runtime.read("state.json")["phase"], "rolled_back_verified")
        self.assertEqual(runtime.read("recovery.json")["attempts"], 1)
        self.assertEqual(sum(event[0] == "create" for event in runtime.events), 1)

    def test_unsafe_new_job_after_failure_keeps_diagnostics_without_stopping_it(self):
        value = manifest()
        runtime = FakeRuntime(self.helper, value)
        rollout = self.helper.Rollout(value, runtime)
        original = runtime.reload_routes
        stop_count = [0]

        def unsafe_failure():
            original()
            runtime.rows = [{"status": "running", "upstream_task_id": "original", "provider_id": "nodyhub", "payload": {"_nody_media_contract": True}}]
            stop_count[0] = sum(event[0] == "stop" for event in runtime.events)
            raise self.helper.RolloutError("injected unsafe state")

        with patch.object(runtime, "reload_routes", side_effect=unsafe_failure), self.assertRaises(self.helper.RolloutError):
            rollout.execute("run")
        self.assertEqual(sum(event[0] == "stop" for event in runtime.events), stop_count[0])
        self.assertTrue(runtime.exists("rollback-error.json"))


if __name__ == "__main__":
    unittest.main()
