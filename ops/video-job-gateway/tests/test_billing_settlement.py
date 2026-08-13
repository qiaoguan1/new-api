import pathlib
import sys
import tempfile
import unittest
import hashlib
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from relay_pricing import RelayPricing
from store import BILLING_CONTRACT_LEGACY, BILLING_CONTRACT_VERSION, Store, StoreConflict


def priced_payload(
    amount: str = "5.961600", contract_version: str = BILLING_CONTRACT_VERSION
) -> str:
    return (
        '{"model":"seedance-2.0","duration":4,'
        '"_billing_v2":true,'
        '"_billing_contract_version":"' + contract_version + '",'
        '"_relay_price":{"contract_version":"xtai-video-pricing-v1",'
        '"currency":"CNY","amount_cny_exact":"' + amount + '",'
        '"official_cost_cny_exact":"3.974400",'
        '"official_cost_cny_per_second_exact":"0.9936",'
        '"fallback_multiplier_exact":"1.5",'
        '"pricing_revision":"ark-2026-08-09","price_source":"ark_official_1_5"}}'
    )


class FixedReservationTests(unittest.TestCase):
    def test_dynamic_marketplace_price_never_overrides_ark_official_times_1_5(self):
        pricing = RelayPricing(ROOT / "relay-pricing.json")

        quote = pricing.official_quote("seedance-2.0", "720p", 4)

        self.assertEqual(quote["amount_cny_exact"], "5.961600")

    def test_480p_rounds_once_at_final_amount_boundary(self):
        quote = RelayPricing(ROOT / "relay-pricing.json").official_quote(
            "seedance-2.0", "480p", 4
        )
        self.assertEqual(quote["official_cost_cny_exact"], "1.767780")
        self.assertEqual(quote["amount_cny_exact"], "2.651670")
        self.assertEqual(quote["cny_per_second_exact"], "0.662918")
        self.assertEqual(quote["price_source"], "ark_official_1_5")
        self.assertEqual(quote["pricing_revision"], "official-fallback-2026-08-09.1")

    def test_unreviewed_official_rate_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "pricing.json"
            raw = (ROOT / "relay-pricing.json").read_text(encoding="utf-8")
            path.write_text(raw.replace('"0.9936"', '"0.01"'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved Ark revision"):
                RelayPricing(path)

    def test_private_authenticated_price_snapshot_keeps_v1_evidence_fields(self):
        snapshot = RelayPricing(ROOT / "relay-pricing.json").snapshot(
            [("seedance-2.0", "720p")]
        )
        text = str(snapshot).lower()
        self.assertIn("cny_per_second_exact", text)
        self.assertIn("official_cost_cny_per_second_exact", text)
        self.assertIn("fallback_multiplier_exact", text)
        self.assertNotIn("markup", text)


class StoreSettlementTests(unittest.TestCase):
    def create_job(
        self,
        store: Store,
        *,
        request_id: str = "billing-request",
        contract_version: str = BILLING_CONTRACT_VERSION,
    ) -> str:
        snapshot, reused = store.create(
            request_id=request_id,
            fingerprint="fingerprint-" + request_id,
            protocol_version="xtai-relay-v1",
            catalog_revision="catalog-test",
            stable_model="seedance-2.0",
            provider_id="paisio",
            upstream_model="sd2-720p",
            adapter_revision="paisio-v1",
            payload_json=priced_payload(contract_version=contract_version),
        )
        self.assertFalse(reused)
        self.assertEqual(snapshot["billing"]["status"], "reserved")
        self.assertEqual(snapshot["billing"]["reserved_amount"], "5.961600")
        return str(snapshot["job_id"])

    def finish_success(self, store: Store, job_id: str) -> None:
        self.assertIsNotNone(store.claim_submit(job_id))
        store.mark_running(job_id, "provider-task-1", "queued", 5)
        store.finish(
            job_id,
            "succeeded",
            result={"type": "url", "source_url": "https://example.com/result.mp4"},
            upstream_task_id="provider-task-1",
            upstream_status="completed",
        )

    def evidence(
        self, job_id: str, *, contract_version: str = BILLING_CONTRACT_VERSION, **overrides
    ):
        value = {
            "contract_version": contract_version,
            "job_id": job_id,
            "revision": 1,
            "provider_task_id": "provider-task-1",
            "actual_cost_status": "actual",
            "actual_cost_cny_exact": "1.450000",
            "evidence_source": "provider_account_ledger",
            "evidence_id": "opaque-evidence-1",
            "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        value.update(overrides)
        material = "\0".join(
            (
                value["contract_version"],
                value["job_id"],
                value["provider_task_id"],
                value["actual_cost_status"],
                value["actual_cost_cny_exact"],
                value["evidence_source"],
                value["evidence_id"],
                value["observed_at"],
            )
        )
        value["evidence_fingerprint"] = hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()
        value["settlement_id"] = hashlib.sha256(
            "\0".join(
                (
                    "xtai-video-settlement-v2",
                    value["job_id"],
                    str(value["revision"]),
                    value["evidence_fingerprint"],
                )
            ).encode("utf-8")
        ).hexdigest()
        return value

    def test_success_holds_result_until_exact_cost_settlement(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.create_job(store)
            self.finish_success(store, job_id)

            pending = store.get(job_id=job_id)
            self.assertEqual(pending["status"], "succeeded")
            self.assertIsNone(pending["result"])
            self.assertEqual(pending["result_delivery"], "pending_settlement")
            self.assertEqual(pending["billing"]["status"], "settlement_pending")
            self.assertIsNone(pending["billing"]["charged_amount"])

            settled, reused = store.apply_settlement(self.evidence(job_id))

            self.assertFalse(reused)
            self.assertEqual(settled["billing"]["status"], "settled")
            self.assertEqual(settled["billing"]["charged_amount"], "2.175000")
            self.assertEqual(settled["billing"]["refund_amount"], "3.786600")
            self.assertEqual(settled["billing"]["supplement_amount"], "0.000000")
            self.assertEqual(settled["result_delivery"], "ready")
            self.assertIsNotNone(settled["result"])
            public_text = str(settled).lower()
            self.assertNotIn("paisio", public_text)
            self.assertNotIn("provider-task-1", public_text)
            self.assertNotIn("actual_cost", public_text)
            self.assertNotIn("margin", public_text)
            self.assertNotIn("markup", public_text)

    def test_same_evidence_replay_is_idempotent_and_conflicts_fail_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.create_job(store)
            self.finish_success(store, job_id)
            evidence = self.evidence(job_id)
            first, reused = store.apply_settlement(evidence)
            self.assertFalse(reused)

            replay, reused = store.apply_settlement(evidence)
            self.assertTrue(reused)
            self.assertEqual(replay["billing"], first["billing"])

            with self.assertRaises(StoreConflict):
                store.apply_settlement(
                    self.evidence(job_id, actual_cost_cny_exact="9.000000")
                )

    def test_historical_v2_task_settles_only_with_its_persisted_contract(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.create_job(
                store,
                request_id="historical-v2-request",
                contract_version=BILLING_CONTRACT_LEGACY,
            )
            self.finish_success(store, job_id)

            with self.assertRaises(StoreConflict):
                store.apply_settlement(self.evidence(job_id))

            settled, reused = store.apply_settlement(
                self.evidence(job_id, contract_version=BILLING_CONTRACT_LEGACY)
            )
            self.assertFalse(reused)
            self.assertEqual(settled["billing"]["contract_version"], BILLING_CONTRACT_LEGACY)
            self.assertEqual(settled["billing"]["status"], "settled")
            with self.assertRaises(StoreConflict):
                store.apply_settlement(
                    self.evidence(
                        job_id,
                        revision=2,
                        provider_task_id="wrong-task",
                    )
                )

    def test_explicit_failure_refunds_frozen_reservation_once(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            job_id = self.create_job(store, request_id="failed-request")
            self.assertIsNotNone(store.claim_submit(job_id))
            store.finish(job_id, "failed", error={"code": "provider_rejected"})

            failed = store.get(job_id=job_id)
            self.assertEqual(failed["billing"]["status"], "refunded")
            self.assertEqual(failed["billing"]["charged_amount"], "0.000000")
            self.assertEqual(failed["billing"]["refund_amount"], "5.961600")
            self.assertIsNone(failed["result"])


if __name__ == "__main__":
    unittest.main()
