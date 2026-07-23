# Verification

## Pre-deployment

- 56 Python monitor tests pass; Python compilation and `git diff --check` pass.
- Prettier, ESLint, TypeScript, and the production frontend build pass.
- The current production inventory contains exactly 35 expected channels and
  reports policy state `pending` with no drift.
- The complete migration executed against production PostgreSQL inside a forced
  rollback transaction; syntax, locks, name guards, and non-name fingerprints
  passed, with zero persistent channel changes.
- The standalone guide patch upgrades the current production HTML, passes
  `--check`, and a second application leaves its SHA-256 unchanged.

## Production

- Full rollback backup created at
  `/opt/ai-api-stack/backups/issue12-channel-names-20260723-2225`, mode 0700;
  its `channels` pg_dump is mode 0600. The migration also wrote a mode-0600
  before-name JSON backup under `/opt/ai-api-stack/backups/channel-name-policy/`.
- All 35 channel names changed in one committed transaction. The before/after
  fingerprint of every non-name field is identical:
  `997d2e1ff2e76f4e6ec97e62e6a663c9`.
- Final database counts: 35 channels, 35 names matching `上游名 · 用途`, and
  35 unique names.
- PackAPI IDs 15/29 and Unity2 IDs 21/22 remain disabled (`status=2`) and both
  providers remain absent from active upstream and credential files.
- Standalone Channel Monitor was upgraded idempotently and contains the naming
  guide. Integrated UI image `new-api-fixed:channel-names-20260723-0429a772`
  (ID `82c984e8...b3b`) contains the same guide and is healthy in production;
  the prior image remains available for rollback.
- Post-rename daily audit remains 11 enabled / 11 healthy / 0 failed. Monitor
  data regenerated at `2026-07-23T22:35:02+08:00`.
- Public `/api/status` is HTTP 200 with `success=true`; `/channel-health` and
  `/channels?action=create` are HTTP 200. Both standalone monitor pages remain
  Basic-Auth protected (401 unauthenticated).
- Paisio remains at 49 historical error rows with maximum log ID 30760, proving
  no new business probe was created.
- Pricing remains unchanged and correct: `gpt-5.6-sol` ratio 5.15/completion 6,
  `gpt-image-2` base price 5.25757, and zero frontend groups differ from 0.15.
- Test user `test_048a0728` remains enabled in `default`; WeChat Pay config and
  key files remain valid/readable.
- Credentials remain mode 0600, the auto-pricing script hash remains
  `ce78d6d...e7b9`, the last pricing run is live for 2026-07-22, and crontab is
  byte-for-byte unchanged.
