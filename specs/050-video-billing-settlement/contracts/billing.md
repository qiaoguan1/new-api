# Contract: Video Billing v2

## Submit/query public billing

```json
{
  "status": "succeeded",
  "result": null,
  "result_delivery": "pending_settlement",
  "billing": {
    "contract_version": "xtai-video-billing-v2",
    "status": "settlement_pending",
    "currency": "CNY",
    "reserved_amount": "5.961600",
    "charged_amount": null,
    "refund_amount": null,
    "supplement_amount": null
  }
}
```

After final settlement the result is released and user-side amounts are exact six-decimal strings.

## Private evidence ingestion

`POST /v1/operations/video-settlements`

```json
{
  "contract_version": "xtai-video-billing-v2",
  "settlement_id": "deterministic-id",
  "job_id": "vjob_...",
  "revision": 1,
  "provider_task_id": "private-provider-task-id",
  "actual_cost_status": "actual",
  "actual_cost_cny_exact": "1.450000",
  "evidence_source": "provider_account_ledger",
  "evidence_id": "opaque-id",
  "observed_at": "2026-08-11T12:30:00+08:00",
  "evidence_fingerprint": "sha256"
}
```

The endpoint is private and bearer-authenticated (`authenticated-billing-evidence-v1`). The SHA-256
fingerprint supplies deterministic identity and replay protection; it is not described as a digital
signature. Exact job/provider-task matching is mandatory. The public job response never includes
the private request fields.
