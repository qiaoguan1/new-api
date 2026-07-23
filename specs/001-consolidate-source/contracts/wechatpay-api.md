# WeChat Pay API Contract

All user-facing endpoints use the repository's existing authentication and JSON response envelope conventions.

## Create Native Payment

`POST /api/user/wechatpay/native/pay`

Creates a pending top-up order and a WeChat Native payment request.

Expected request fields include the selected top-up amount and any existing wallet fields required by the common payment flow.

Successful response includes:

- internal trade number
- native payment QR code URL
- order status suitable for polling

Validation failures, disabled configuration, provider errors, or unauthorized access return the normal API error envelope without exposing credentials or provider signatures.

## Query Order

`GET /api/user/wechatpay/order/{trade_no}`

Returns the authenticated user's current order state. If needed, the handler may reconcile a pending local state with WeChat Pay before responding.

Contract rules:

- A user cannot inspect another user's order.
- Successful reconciliation is idempotent.
- Unknown or malformed trade numbers return the normal API error envelope.

## Payment Notification

`POST /api/wechatpay/notify`

Receives WeChat Pay notifications. This endpoint is intentionally unauthenticated at the application-session layer and instead requires valid WeChat Pay signature verification and notification decryption.

Contract rules:

- Reject invalid signatures, malformed ciphertext, mismatched orders, or mismatched amounts.
- Credit a valid paid order exactly once.
- Return the provider-compatible acknowledgement without revealing internal details.

## Top-up Information Extension

Existing top-up information responses may expose whether WeChat Pay is enabled and the non-secret display data required by the wallet UI. Private keys, API v3 keys, raw certificates, and other secrets must never appear in the response.
