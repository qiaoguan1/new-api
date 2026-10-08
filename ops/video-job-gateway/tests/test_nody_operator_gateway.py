"""Explicit manual-testing holds never replace evidence or legacy admission."""

import copy
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapters import ProviderConfig
from app import Config, Gateway, GatewayError
from nodyhub import NODY_MODELS, NODY_IMAGE_MODELS, NodyHubAdapter
from reference_contract import ReferenceContractError
from store import BILLING_CONTRACT_REFERENCE_VERSION, _reservation_from_payload, build_settlement_evidence


def policy(rate="0.200000"):
    return {"schema_version": "xtai-nody-operator-testing-v1", "revision": "operator-fixture", "enabled": True,
            "source_snapshot": {"endpoint": "https://nodyhub.com/api/pricing", "observed_at": "2026-10-08T01:00:00Z",
                                "sha256": "a" * 64, "source_unit": "display_credit", "billing_unit": "per_second"},
            "currency_factor_exact": "1.500000", "group_factor_exact": "2.000000", "markup_exact": "1.500000",
            "max_reserve_cny_exact": "150.000000",
            "models": [{"model": model, "max_source_rate_exact": rate, "source_row_sha256": "b" * 64} for model in NODY_MODELS]}


def body(**changes):
    result = {"provider_id": "video-aixingtu-api", "request_id": "operator-frame", "model": "wan3.0-video", "prompt": "blue ball",
              "resolution": "720p", "duration": 6, "mode": "last_frame", "aspect_ratio": "16:9", "generate_audio": False,
              "images": ["https://media.example/image.png"], "image_roles": ["last"], "image_identities": ["c" * 64],
              "reference_videos": [], "reference_audios": []}
    result.update(changes)
    return result


def video():
    return {"url": "https://media.example/video.mp4?signature=one", "role": "reference_video", "sha256": "d" * 64,
            "size_bytes": 128, "duration_seconds": "1.000000", "mime_type": "video/mp4", "width_pixels": 640, "height_pixels": 360}


@contextmanager
def isolated():
    """Patch constructor recovery and monitor submission before opening any DB."""
    with tempfile.TemporaryDirectory() as directory, patch.object(Gateway, "start_submit"):
        yield pathlib.Path(directory)


class OperatorGatewayTests(unittest.TestCase):
    def test_candidate_capabilities_enable_existing_video_audio_fields_without_fake_specs(self):
        with isolated() as root:
            gateway, _ = self.gateway(root)
            rows = {row['id']: row for row in gateway.capabilities(BILLING_CONTRACT_REFERENCE_VERSION)['capabilities']['video']['models']}
            for name, videos, audios in (('wan3.0-video', 5, 5), ('wan3.0-video-prime', 5, 5), ('omni-flash', 1, 0)):
                self.assertTrue(rows[name]['reference_video']['available'])
                self.assertEqual(rows[name]['reference_video']['max_count'], videos)
                self.assertEqual(rows[name]['reference_video']['verification_status'], 'unverified')
                self.assertEqual(rows[name]['reference_video']['specifications'], [])
                self.assertEqual(rows[name]['max_audios'], audios)
            self.assertTrue(rows['wan3.0-video-prime']['reference_audio']['available'])
            self.assertFalse(rows['wan3.0-video-prime']['reference_audio']['requires_non_audio_input'])
            self.assertFalse(rows['omni-flash']['reference_audio']['available'])
            self.assertTrue(rows['flux-3-video']['image_reference']['available'])
            self.assertEqual(rows['flux-3-video']['image_reference']['specifications'], [])
            self.assertFalse(rows['wan3.0-video']['generate_audio_required'])

    def gateway(self, root, *, data=None, exact=False, operator=True, images=False):
        path = root / "operator.json"
        path.write_text(json.dumps(policy() if data is None else data), encoding="utf-8")
        exact_path = None
        if exact:
            exact_path = root / "exact.json"
            exact_path.write_text(json.dumps({"schema_version": "xtai-nody-media-input-v1", "revision": "exact-first", "profiles": [{
                "model": "wan3.0-video", "mode": "reference", "resolution": "480p", "duration": 2,
                "image_count": 0, "video_count": 1, "audio_count": 0, "generate_audio": True, "aspect_ratio": "16:9",
                "input_video_seconds_exact": "1.000000", "actual_cost_cny_exact": "0.500000", "status": "succeeded", "cost_status": "actual",
                "evidence_source": "nodyhub_authenticated_video_task", "evidence_task_id": "d4c04b41-987d-42cd-bf8e-ac89b66060d0"}]}), encoding="utf-8")
        provider = ProviderConfig("nodyhub", "https://nodyhub.com", "test", ("getapib.org",))
        image_path = None
        if images:
            image_path = root / "images.json"
            image_path.write_text(json.dumps({"schema_version": "xtai-nody-image-input-v1", "revision": "six-images", "profiles": [{
                "model": model, "mode": mode, "image_count": count, "resolution": resolution, "duration": duration,
                "actual_cost_cny_exact": "0.300000", "status": "succeeded", "cost_status": "actual",
                "evidence_source": "nodyhub_authenticated_video_task", "evidence_task_id": "d4c04b41-987d-42cd-bf8e-ac89b66060d0"}
                for model, (resolution, duration) in NODY_IMAGE_MODELS.items() for mode, count in (("reference", 1), ("all_reference", 2))]}), encoding="utf-8")
        config = Config(token="test", data_dir=root / "state", catalog_file=ROOT / "catalog.json", providers={"nodyhub": provider},
                        pricing_file=ROOT / "relay-pricing.json", public_base_url="https://api.aixingtuyun.com",
                        v21_approved_providers=frozenset({"nodyhub"}), reference_media_hosts=("media.example",),
                        nody_image_contract_file=image_path, nody_media_contract_file=exact_path, nody_operator_testing_file=path if operator else None)
        verifier = SimpleNamespace(verify_images=Mock(), verify=Mock())
        gateway = Gateway(config, adapters={"nodyhub": NodyHubAdapter(provider)}, billing_collectors={"nodyhub": SimpleNamespace(ready=True)},
                          reference_verifier=verifier, start_monitor=False)
        gateway.start_submit = Mock()
        return gateway, verifier

    def frozen(self, gateway, raw):
        snapshot, reused = gateway.submit_v22(raw, idempotency_key=raw["request_id"])
        self.assertFalse(reused)
        row = gateway.store.get(job_id=snapshot["job_id"], internal=True)
        return snapshot, row, json.loads(row["payload_json"])

    def test_config_is_opt_in_and_maps_only_the_new_explicit_file(self):
        with patch.dict("os.environ", {"VIDEO_JOB_GATEWAY_TOKEN": "test"}, clear=True):
            self.assertIsNone(Config.from_env().nody_operator_testing_file)
        with patch.dict("os.environ", {"VIDEO_JOB_GATEWAY_TOKEN": "test", "VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE": "operator.json"}, clear=True):
            self.assertEqual(Config.from_env().nody_operator_testing_file, pathlib.Path("operator.json").resolve())

    def test_preflight_is_truthful_retail_only_and_verifies_before_any_hold(self):
        with isolated() as root:
            gateway, verifier = self.gateway(root)
            result = gateway.preflight_nody_media(body())
            self.assertEqual(result["reserved_cny_exact"], "5.400000")
            self.assertEqual(result["price_source"], "nodyhub_operator_testing_estimate")
            self.assertEqual(result["pricing_kind"], "estimated_reservation")
            self.assertEqual(result["verification_status"], "unverified")
            self.assertEqual(result["admission_mode"], "operator_testing")
            self.assertFalse(result["is_upper_bound"])
            self.assertFalse(result["reserve_cap_applied"])
            self.assertRegex(result["policy_digest"], r"^[0-9a-f]{64}$")
            self.assertFalse(result["task_created"])
            self.assertIsNone(gateway.store.get(request_id=body()["request_id"]))
            gateway.start_submit.assert_not_called()
            verifier.verify_images.assert_called_once()
            for private in ("operator_policy_evidence", "source_snapshot", "source_row_sha256", "actual_cost_cny_exact", "reference_cost_cny_exact"):
                self.assertNotIn(private, json.dumps(result))

    def test_exact_media_and_legacy_text_remain_first_choice(self):
        with isolated() as root:
            gateway, _ = self.gateway(root, exact=True)
            exact = body(request_id="exact-wins", mode="reference", resolution="480p", duration=2, generate_audio=True,
                         images=[], image_roles=[], image_identities=[], reference_videos=[video()])
            result = gateway.preflight_nody_media(exact)
            self.assertEqual(result["reserved_cny_exact"], "0.750000")
            self.assertNotIn("pricing_kind", result)
            text = body(request_id="legacy-wins", mode="text", resolution="480p", duration=2, generate_audio=True,
                        images=[], image_roles=[], image_identities=[])
            snapshot, _, frozen = self.frozen(gateway, text)
            self.assertEqual(snapshot["billing"]["reserved_amount"], "1.125000")
            self.assertNotIn("_nody_operator_testing", frozen)

    def test_broader_text_is_candidate_validated_without_changing_old_text(self):
        with isolated() as root:
            gateway, verifier = self.gateway(root)
            raw = body(mode="text", resolution="480p", duration=3, generate_audio=True, images=[], image_roles=[], image_identities=[])
            snapshot, _, frozen = self.frozen(gateway, raw)
            self.assertTrue(frozen["_nody_operator_testing"])
            self.assertEqual(snapshot["billing"]["reserved_amount"], "2.700000")
            self.assertEqual(gateway.adapters["nodyhub"].request_body(raw["model"], frozen)["duration"], 3)
            verifier.verify_images.assert_not_called(); verifier.verify.assert_not_called()

    def test_verified_hash_and_strict_av_deadline_are_required_before_freeze(self):
        with isolated() as root:
            gateway, verifier = self.gateway(root)
            raw = body(mode="reference", resolution="480p", duration=2, generate_audio=True, images=[], image_roles=[], image_identities=[], reference_videos=[video()])
            verifier.verify.side_effect = ReferenceContractError("reference_video_identity_mismatch", "wrong digest")
            with self.assertRaises(GatewayError) as caught:
                gateway.submit_v22(raw, idempotency_key=raw["request_id"])
            self.assertEqual(caught.exception.status, 409)
            self.assertTrue(caught.exception.before_task)
            self.assertIsNone(gateway.store.get(request_id=raw["request_id"]))
            self.assertEqual(verifier.verify.call_args.kwargs["duration_tolerance"], Decimal("0.000001"))
            self.assertIn("deadline", verifier.verify.call_args.kwargs)
            gateway.start_submit.assert_not_called()

    def test_frozen_operator_replay_survives_removed_policy_and_unready_provider(self):
        with isolated() as root:
            gateway, verifier = self.gateway(root)
            raw = body(mode="reference", resolution="480p", duration=2, generate_audio=True, images=[], image_roles=[], image_identities=[], reference_videos=[video()])
            first, _, frozen = self.frozen(gateway, raw)
            restarted = Gateway(replace(gateway.config, nody_operator_testing_file=None), adapters=gateway.adapters,
                                billing_collectors={"nodyhub": SimpleNamespace(ready=False)}, reference_verifier=verifier, start_monitor=False)
            restarted.start_submit = Mock()
            rotated = copy.deepcopy(raw); rotated["reference_videos"][0]["url"] = "https://media.example/video.mp4?signature=two"
            second, reused = restarted.submit_v22(rotated, idempotency_key=raw["request_id"])
            self.assertTrue(reused)
            self.assertEqual(second["job_id"], first["job_id"])
            restarted.start_submit.assert_not_called()
            verifier.verify.assert_called_once()
            self.assertNotIn("url", frozen["reference_input"]["reference_videos"][0])
            rotated["reference_videos"][0]["sha256"] = "e" * 64
            with self.assertRaises(GatewayError) as caught:
                restarted.submit_v22(rotated, idempotency_key=raw["request_id"])
            self.assertEqual(caught.exception.status, 409)

    def test_estimate_never_becomes_fake_final_cost_and_actual_bill_can_supplement_cap(self):
        with isolated() as root:
            gateway, _ = self.gateway(root, data=policy("100.000000"))
            snapshot, row, frozen = self.frozen(gateway, body())
            self.assertEqual(snapshot["billing"]["reserved_amount"], "150.000000")
            self.assertEqual(row["official_cost_cny_exact"], "")
            self.assertFalse(frozen["_relay_price"]["is_upper_bound"])
            self.assertTrue(frozen["_relay_price"]["reserve_cap_applied"])
            self.assertNotIn("billing", gateway._result(row, "https://getapib.org/video.mp4", False))
            gateway.store.claim_submit(snapshot["job_id"])
            task = "d4c04b41-987d-42cd-bf8e-ac89b66060d0"
            gateway.store.mark_running(snapshot["job_id"], task, "running", 5)
            gateway.store.finish(snapshot["job_id"], "succeeded", result={"source_url": "https://getapib.org/video.mp4"})
            evidence = build_settlement_evidence(job_id=snapshot["job_id"], revision=1, provider_task_id=task, actual_cost_status="actual",
                                                actual_cost_cny_exact="120.000000", evidence_source="nodyhub_authenticated_video_task", evidence_id="fixture-bill",
                                                observed_at="2026-10-08T01:00:00+00:00", contract_version=BILLING_CONTRACT_REFERENCE_VERSION)
            settled, _ = gateway.store.apply_settlement(evidence)
            self.assertEqual(settled["billing"]["charged_amount"], "180.000000")
            self.assertEqual(settled["billing"]["supplement_amount"], "30.000000")

    def test_store_rejects_forged_source_digest_tuple_flags_and_calculation(self):
        with isolated() as root:
            gateway, _ = self.gateway(root)
            _, _, frozen = self.frozen(gateway, body())
            self.assertEqual(_reservation_from_payload(json.dumps(frozen))["status"], "reserved")
            for change in ({"price_source": "ark_official_1_5"}, {"policy_digest": "0" * 64}, {"amount_cny_exact": "0.000001"},
                           {"output_seconds": 7}, {"admission_mode": "verified"}, {"is_upper_bound": True}, {"reserve_cap_applied": True}):
                altered = copy.deepcopy(frozen); altered["_relay_price"].update(change)
                with self.subTest(change=change):
                    self.assertNotEqual(_reservation_from_payload(json.dumps(altered))["status"], "reserved")

    def test_public_testing_rules_are_not_fabricated_exact_profiles_or_wallet_gated(self):
        with isolated() as root:
            gateway, _ = self.gateway(root)
            caps = gateway.capabilities("xtai-video-billing-v2.2")
            row = next(row for row in caps["capabilities"]["video"]["models"] if row["id"] == "wan3.0-video")
            self.assertTrue(row["operator_testing"]["supported"])
            self.assertTrue(row["operator_testing"]["available"])
            self.assertEqual(row["operator_testing"]["verification_status"], "unverified")
            self.assertEqual(row["media_reference"]["specifications"], [])
            prices = gateway.video_prices()
            self.assertTrue(prices["operator_testing"]["enabled"])
            self.assertEqual(prices["media_reference_pricing"]["models"], [])
            self.assertNotIn("operator_policy_evidence", json.dumps(prices))
            self.assertNotIn("wallet", json.dumps(row["operator_testing"]).lower())

    def test_definitive_operator_payload_failures_do_not_quarantine_legacy_routes(self):
        with isolated() as root:
            gateway, _ = self.gateway(root)
            for index in range(3):
                snapshot, _, _ = self.frozen(gateway, body(request_id=f"operator-invalid-{index}"))
                gateway.store.claim_submit(snapshot["job_id"])
                gateway.store.finish(snapshot["job_id"], "failed", error={"code": "nodyhub_submit_rejected", "category": "validation", "http_status": 400,
                                                                           "message": "invalid input frame", "uncertain": False})
            self.assertNotIn("nodyhub", gateway.store.unhealthy_providers())
            self.assertIn("nodyhub", gateway.eligible_v2_providers)

    def test_operator_account_failures_still_protect_all_routes(self):
        for category, code, status, message in (("authentication", "invalid_api_key", 401, "invalid key"),
                                               ("validation", "insufficient_quota", 400, "insufficient quota")):
            with self.subTest(category=category), isolated() as root:
                gateway, _ = self.gateway(root)
                queued = []
                for index in range(3):
                    snapshot, _, _ = self.frozen(gateway, body(request_id=f"operator-account-{index}"))
                    queued.append(snapshot)
                for snapshot in queued:
                    gateway.store.claim_submit(snapshot["job_id"])
                    gateway.store.finish(snapshot["job_id"], "failed", error={"code": code, "category": category, "http_status": status,
                                                                               "message": message, "uncertain": False})
                self.assertIn("nodyhub", gateway.store.unhealthy_providers())

    def test_feature_off_preserves_legacy_quote_and_failure_counters(self):
        with isolated() as root:
            gateway, _ = self.gateway(root, operator=False)
            raw = body(mode="text", resolution="480p", duration=2, generate_audio=True, images=[], image_roles=[], image_identities=[])
            self.assertNotIn("operator_testing", gateway.video_prices())
            queued = []
            for index in range(3):
                snapshot, _, frozen = self.frozen(gateway, {**raw, "request_id": f"legacy-bad-{index}"})
                self.assertEqual(snapshot["billing"]["reserved_amount"], "1.125000")
                self.assertNotIn("_nody_operator_testing", frozen)
                queued.append(snapshot)
            for snapshot in queued:
                gateway.store.claim_submit(snapshot["job_id"])
                gateway.store.finish(snapshot["job_id"], "failed", error={"code": "invalid_content", "category": "validation", "http_status": 400,
                                                                           "message": "invalid frame", "uncertain": False})
            self.assertIn("nodyhub", gateway.store.unhealthy_providers())

    def test_all_six_legacy_grok_images_still_win_over_operator_estimates(self):
        with isolated() as root:
            gateway, _ = self.gateway(root, images=True)
            for model, (resolution, duration) in NODY_IMAGE_MODELS.items():
                for mode, count in (("reference", 1), ("all_reference", 2)):
                    raw = body(request_id=f"{model}-{count}", model=model, mode=mode, resolution=resolution, duration=duration,
                               generate_audio=True, images=[f"https://media.example/grok{index}.png" for index in range(count)],
                               image_roles=["reference"] * count, image_identities=["c" * 64] * count)
                    raw.pop("reference_videos"); raw.pop("reference_audios")
                    with self.subTest(model=model, count=count):
                        snapshot, _, frozen = self.frozen(gateway, raw)
                        self.assertEqual(snapshot["billing"]["reserved_amount"], "0.450000")
                        self.assertIn("image_contract_evidence", frozen["_relay_price"])
                        self.assertNotIn("_nody_operator_testing", frozen)

    def test_invalid_optional_operator_file_does_not_disable_proven_text(self):
        with isolated() as root:
            gateway, _ = self.gateway(root, data={"enabled": True})
            self.assertFalse(gateway.nody_operator_testing.enabled)
            self.assertEqual(gateway.nody_operator_testing_error, "operator_testing_not_ready")
            snapshot, _, frozen = self.frozen(gateway, body(mode="text", resolution="480p", duration=2, generate_audio=True,
                                                          images=[], image_roles=[], image_identities=[]))
            self.assertEqual(snapshot["billing"]["reserved_amount"], "1.125000")
            self.assertNotIn("_nody_operator_testing", frozen)

    def test_uncertain_operator_replay_retains_hold_without_new_submission(self):
        with isolated() as root:
            gateway, verifier = self.gateway(root)
            first, _, _ = self.frozen(gateway, body())
            gateway.store.claim_submit(first["job_id"])
            gateway.store.finish(first["job_id"], "uncertain", error={"code": "unknown_submit", "uncertain": True})
            replay, reused = gateway.submit_v22(body(), idempotency_key=body()["request_id"])
            self.assertTrue(reused)
            self.assertEqual(replay["status"], "uncertain")
            self.assertEqual(replay["billing"]["status"], "pending_review")
            self.assertEqual(replay["billing"]["reserved_amount"], "5.400000")
            self.assertIsNone(replay["billing"]["charged_amount"])
            gateway.start_submit.assert_called_once()
            verifier.verify_images.assert_called_once()

    def test_actual_authenticated_bill_refunds_the_unused_estimated_hold(self):
        with isolated() as root:
            gateway, _ = self.gateway(root)
            snapshot, _, _ = self.frozen(gateway, body())
            gateway.store.claim_submit(snapshot["job_id"])
            task = "d4c04b41-987d-42cd-bf8e-ac89b66060d0"
            gateway.store.mark_running(snapshot["job_id"], task, "running", 5)
            gateway.store.finish(snapshot["job_id"], "succeeded", result={"source_url": "https://getapib.org/video.mp4"})
            evidence = build_settlement_evidence(job_id=snapshot["job_id"], revision=1, provider_task_id=task, actual_cost_status="actual",
                                                actual_cost_cny_exact="1.000000", evidence_source="nodyhub_authenticated_video_task", evidence_id="fixture-refund-bill",
                                                observed_at="2026-10-08T01:00:00+00:00", contract_version=BILLING_CONTRACT_REFERENCE_VERSION)
            settled, _ = gateway.store.apply_settlement(evidence)
            self.assertEqual(settled["billing"]["reserved_amount"], "5.400000")
            self.assertEqual(settled["billing"]["charged_amount"], "1.500000")
            self.assertEqual(settled["billing"]["refund_amount"], "3.900000")


if __name__ == "__main__":
    unittest.main()
