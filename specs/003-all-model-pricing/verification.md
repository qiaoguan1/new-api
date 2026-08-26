# Verification Report: All-Channel, All-Model Automatic Pricing

**Verified**: 2026-07-23
**Commit**: `9dedcd56`
**Pull request**: https://github.com/qiaoguan1/new-api/pull/4

## Automated Checks

- Python unit tests: 9/9 passed locally and in the server test workspace.
- `go test ./setting/...`: passed.
- `go test ./...`: passed with temporary untracked frontend embed placeholders; placeholders were
  removed after the run.
- `gofmt -d`: clean.
- `git diff --check`: passed.
- Change-set secret scan: no credential, token, private key, runtime ledger, or log was found.

## Production Dry Run

- Target billing day: `2026-07-22`.
- Enabled-channel models discovered: 729.
- Models with a healthy eligible channel and trusted model-level actual cost: 5.
- Models retained without change: 724.
- A critical model alert and failed image channels correctly prevented affected price changes.

## Applied Decisions

| Model | Billing | Customer price after group ratio 0.15 |
|---|---|---:|
| `gpt-5.5` | token input/output | CNY 11.25 / 90.00 per million |
| `gpt-5.6-sol` | token input/output | CNY 1.545 / 9.27 per million |
| `grok-video-3` | fixed per call | CNY 2.25 |
| `omni-flash` | fixed per call | CNY 6.30 |
| `wan2.6-t2v` | fixed per call | CNY 11.25 |

Each value equals the highest eligible upstream actual cost multiplied by 1.5.

## Deployment Evidence

- Pre-change rollback archive: `/root/maintenance-backups/20260723-172921-issue3-pricing`.
- Pricing backup: `/opt/ai-api-stack/channel-monitor/backups/pricing/` with mode `0600`.
- Deployed image ID: `sha256:8dd4ab72dbdbdfa2e7693d49f1c6b8865f130d0e6f49e8ea0fb358192217d2f3`.
- Production worker SHA-256: `02087c851ccba9adcb78e1bb4448ece8d707f33ac0173b65da206598916eea8e`.
- NewAPI container: healthy.
- Internal `/api/status`: success.
- `gpt-5.6-sol` runtime completion ratio: 6 (previous hard-coded value 8 no longer overrides).
- Fixed-price runtime entries: `grok-video-3=15`, `omni-flash=42`, `wan2.6-t2v=75` before the 0.15 group ratio.
- Cron service: active; 08:40 job still uses `flock` and the deployed worker path.
- Recent production log scan: zero panic/fatal entries after deployment.

## Rollback

The old script, pricing JSON, Compose file, crontab, source file, and image ID are preserved in the
pre-change archive. The previous container image is tagged
`new-api-fixed:rollback-issue3-20260723-172921`.
