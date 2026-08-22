# Specification: stable text and image model mapping

## Goal

Expose a small fixed set of XingTu text and image model names while translating
each request and each pricing lookup to the selected upstream model name.

## Stable names

- `gpt-5.5`
- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-image-2`
- `banana-flash`
- `banana-pro`

## Requirements

1. Active text/image channel `models`, `model_mapping`, and `abilities` publish
   only the stable names assigned to that channel.
2. Code Plan maps `gpt-image-2` to `gpt-image-2-auto`.
3. Paisio maps `banana-flash` and `banana-pro` to the authenticated Gemini
   image model names.
4. Pricing resolves actual and catalog evidence through the same stable to
   upstream mapping used by request relay. Conflicting price keys fail closed.
5. Video channels, the v2 video catalog, and Topaz are byte-for-byte unchanged.
6. Production changes require a full backup, clean ability rebuild, dry-run,
   idempotence verification, and ten no-charge health rounds.

## Acceptance

- Downstream abilities contain the stable names and do not contain the replaced
  upstream aliases for the affected channels.
- Request mapping and catalog lookup use the same upstream target.
- `gpt-image-2` can use Code Plan `gpt-image-2-auto` cost evidence.
- Banana stable names retain Paisio catalog prices.
- No video or Topaz channel row or ability changes.

## Safety boundaries

- Do not infer ambiguous `gpt-5.6` or compact aliases.
- Do not send paid generation requests.
- Do not expose upstream credentials.
