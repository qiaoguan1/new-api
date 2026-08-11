# Contract Decisions

## Downstream provides once

- One public HTTPS callback URL on port 443.
- A server endpoint accepting signed JSON POSTs and returning HTTP 2xx after durable receipt.
- Durable `event_id` uniqueness and the ability to process events out of order.
- A secure location for the signing secret supplied by the relay operator.
- An operations contact and, if required, firewall allowlisting for the relay egress IP.

## Relay provides once

- API base URL and downstream bearer token.
- Contract version `xtai-video-billing-v2` and provider ID `video-aixingtu-api`.
- Approved stable model/resolution list and pricing revision.
- A random callback signing secret delivered outside application logs/source control.
- Event/header/signature schema, retry policy, and polling fallback.

## Callback headers

```http
Content-Type: application/json
User-Agent: XingTuVideoWebhook/1
X-XingTu-Contract-Version: xtai-video-billing-v2
X-XingTu-Event-ID: evt_...
X-XingTu-Timestamp: 1786400000
X-XingTu-Delivery-Attempt: 1
X-XingTu-Signature: v1=<lowercase-hex-hmac-sha256>
```

Signature input is the UTF-8 bytes of the decimal timestamp, one ASCII dot, then the exact received
raw body. Downstream rejects timestamps outside five minutes, verifies the MAC in constant time,
then inserts `event_id` under a unique constraint before processing.

