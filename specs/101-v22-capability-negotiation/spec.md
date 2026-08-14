# Specification: v2.2 capability contract negotiation

## Goal

The authenticated capability catalog advertises the billing contract selected
by the downstream request, so a v2.2 client can safely expose the seven relay
model/resolution combinations without breaking v2.1 clients.

## Requirements

1. `GET /v1/capabilities` reads `X-XingTu-Contract-Version` after bearer
   authentication.
2. A supported v2.1 or v2.2 request returns that exact value in both the
   top-level `billing_contract_version` and
   `capabilities.video.billing_contract_version` fields.
3. An absent contract header preserves the existing v2.1 default response.
4. An unsupported non-empty contract header returns HTTP 400 with the existing
   structured `unsupported_contract_version` error contract.
5. Capability negotiation does not change `protocol_version`, readiness,
   models, resolutions, reference-media capabilities, routing, pricing,
   billing, settlement, or webhook behavior.
6. `GET /v1/video-prices` remains `xtai-video-pricing-v1`, CNY, and publishes
   exactly seven model/resolution rows whose billing unit is `output_second`.

## Non-goals

- Changing any reservation or settlement amount.
- Removing v2.1 compatibility.
- Creating a paid video task.
- Modifying provider routes or credentials.

## Acceptance

- Regression tests reproduce the production mismatch before the fix.
- v2.1, v2.2, absent-header, and unsupported-header behavior is deterministic.
- The full video gateway test suite passes.
- Staging and production pass ten no-charge capability/price verification rounds.
