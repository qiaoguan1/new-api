# v2 to v2.1 Migration Contract

- New XingTu submissions send `X-XingTu-Contract-Version: xtai-video-billing-v2.1`.
- Existing v2 task IDs remain queryable with the v2 header while they finish.
- New submissions carrying the v2 header receive `unsupported_contract_version` and do not reserve
  or submit work.
- Both versions require explicit `generate_audio`; `true` remains supported.
- v2.1 never exposes `settled_with_debt` as a final state. A legacy debt record is read as
  `payment_required` and its content remains unavailable.
- Oversize XingTu-tagged create requests return HTTP 413 with a stable `request_too_large` error
  before JSON decoding or billing, including obsolete or unknown version headers.
