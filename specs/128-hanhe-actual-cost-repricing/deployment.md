# Production deployment record

## Deployment

- Deployed: 2026-08-22, Asia/Shanghai
- Source PR: #129
- Production backup:
  `/opt/ai-api-stack/backups/issue128-actual-cost-20260822-115152`
- Automatic pricing backup:
  `/opt/ai-api-stack/channel-monitor/backups/pricing/pricing-options-2026-08-21-20260822T115623+0800.json`

## Reconciliation

- Hanhe rows: 465
- Billed `actual_cost`: CNY 18.52698844
- Undiscounted `total_cost`: CNY 92.6349422
- GPT-5.5 actual unit cost: CNY 1 input / 6 output per million Token
- GPT-5.6 Sol actual unit cost: CNY 1 input / 6 output per million Token

## Price write

- GPT-5.5 `ModelRatio`: 25 -> 5; `CompletionRatio`: 6 unchanged
- GPT-5.6 Sol `ModelRatio`: 25 -> 5; `CompletionRatio`: 6 unchanged
- Customer price with group ratio 0.15: CNY 1.5 input / 9 output per million Token
- Three catalog-backed fixed models were revalidated without value changes.
- No video pricing value changed.

## Verification

- Full channel-monitor tests: 161/161 passed
- Formal run: 5 apply decisions, 60 fail-closed skips
- Second dry-run: proposed and current values identical
- Public no-charge health checks: 10/10 rounds passed
- NewAPI and all health-equipped core containers healthy
