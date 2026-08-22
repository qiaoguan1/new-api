# Production deployment record

## Deployment

- Deployed: 2026-08-22, Asia/Shanghai
- Source PR: #132
- Backup: `/opt/ai-api-stack/backups/issue131-stable-mapping-20260822-135135`

## Stable abilities

- Text: `gpt-5.5`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- Image: `gpt-image-2`
- Banana: `banana-flash`, `banana-pro`

## Request and price mappings

- Code Plan: `gpt-image-2` -> `gpt-image-2-auto`
- Paisio: `banana-flash` -> `gemini-3.1-flash-image-preview`
- Paisio: `banana-pro` -> `gemini-3-pro-image-preview`

## Prices

- `gpt-image-2`: CNY 0.15 per image
- `banana-flash`: CNY 0.105 per image
- `banana-pro`: CNY 0.15 per image
- GPT-5.5 and GPT-5.6 Sol remain CNY 1.5 input / 9 output per million Token

## Verification

- Full channel-monitor tests: 176/176 passed
- Request mapping and audited price keys matched
- Disabled enabled-ability count: 0
- Replaced upstream-alias ability count: 0
- Video and Topaz channel/ability hashes unchanged
- Second dry-run was idempotent
- Public no-charge health rounds: 10/10 passed
