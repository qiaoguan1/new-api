# Data model

## SearchRequest

- `id: string` — required client action identifier.
- `model: string` — required routing and billing model.
- `reasoning: object?` — opaque compatible reasoning options.
- `input: string | array?` — opaque Codex input.
- `commands: object?` — opaque standalone search operations.
- `settings: object?` — opaque search settings.
- `max_output_tokens: uint?` — optional and bounded.

## SearchResponse

- `encrypted_output: string?`
- `output: string` — required.
- `results: array<any>?` — opaque for forward compatibility.

No database migration is required.
