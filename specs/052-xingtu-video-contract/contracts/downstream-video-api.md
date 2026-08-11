# Contract: XingTu Downstream Video API v2

## Activation and authentication

Every request uses:

```http
Authorization: Bearer <xingtu-api-token>
X-XingTu-Contract-Version: xtai-video-billing-v2
```

Every submit additionally uses:

```http
Idempotency-Key: <stable-request-id>
Content-Type: application/json
```

The JSON `request_id` must equal `Idempotency-Key`.

## Submit

```json
{
  "provider_id": "video-aixingtu-api",
  "request_id": "req_20260811_000001",
  "model": "seedance-2.0",
  "resolution": "720p",
  "duration": 4,
  "aspect_ratio": "16:9",
  "generate_audio": true,
  "prompt": "用户提示词"
}
```

## Public response invariants

- Amounts are six-decimal CNY strings.
- `status` describes generation; `billing.status` describes money movement.
- `result` is null until `result_delivery=ready`.
- `charged_amount` is the final user charge, not provider cost.
- Provider, route, provider task ID, actual cost, margin, credentials, and evidence are private.

## Idempotency

- Same key and same normalized request: reuse the original public task.
- Same key and different request: HTTP 409 `idempotency_conflict`.
- A concurrent or uncertain claim never triggers another provider submission.

